"""Analyze canonical local Supabase snapshots and publish validated typed results.

This command is deliberately local-only. It reads a complete database snapshot, closes
the read transaction before running the existing analyzer, writes one immutable run in a
short transaction, and promotes the existing latest-JSON key only after validation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis_runtime import latest_blob_path  # noqa: E402
from mix_registry import MIX_SPECS, resolve_mix  # noqa: E402
from piu_misgrade_analyzer import (  # noqa: E402
    SCRIPT_VERSION,
    AnalysisConfig,
    analyze_snapshot,
    build_web_payload,
)
from pumbility_store import (  # noqa: E402
    PumbilityArtifactStore,
    _assert_schema,
    require_loopback_database_url,
)
from scripts.reconcile_pumbility_supabase import _database_snapshot  # noqa: E402


DEFAULT_BOOTSTRAP_SAMPLES = 500
METHODOLOGY_FAMILY = "pumbility-chart-difficulty"


@dataclass(frozen=True)
class DatabaseInput:
    mix_key: str
    mix_id: Any
    snapshot: dict[str, Any]
    player_by_short_hash: dict[str, tuple[Any, str]]
    chart_ids: dict[str, Any]


@dataclass(frozen=True)
class AnalysisOutput:
    database_input: DatabaseInput
    config: AnalysisConfig
    started_at: datetime
    payload: dict[str, Any]
    baselines: list[dict[str, Any]]
    contributions: list[dict[str, Any]]
    chart_results: list[dict[str, Any]]
    source_hash: str
    output_hash: str


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_hash(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _frame_records(frame: Any) -> list[dict[str, Any]]:
    return list(json.loads(frame.to_json(orient="records", double_precision=15)))


def _nullable_number(value: Any, *, integer: bool = False) -> int | float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric):
        return None
    return int(numeric) if integer else numeric


def _read_database_input(connection: Any, mix_key: str) -> DatabaseInput:
    """Read the canonical snapshot and its private UUID mappings in one short DB scope."""
    snapshot = _database_snapshot(connection, mix_key)
    with connection.cursor() as cursor:
        cursor.execute("select id from pumbility.mixes where mix_key = %s", (mix_key,))
        mix_row = cursor.fetchone()
        if mix_row is None:
            raise RuntimeError(f"The selected mix is not present in the local Pumbility schema.")
        mix_id = mix_row[0]
        cursor.execute(
            """
            select p.id, p.upstream_player_id
            from pumbility.player_consents pc
            join pumbility.players p on p.id = pc.player_id
            join pumbility.consent_scopes cs on cs.id = pc.consent_scope_id
            where pc.mix_id = %s and cs.scope_key = 'analysis' and pc.valid_to is null
            order by p.upstream_player_id
            """,
            (mix_id,),
        )
        player_rows = list(cursor.fetchall())
        cursor.execute(
            """
            select id, upstream_chart_id
            from pumbility.charts
            where mix_id = %s and is_active
            order by upstream_chart_id
            """,
            (mix_id,),
        )
        chart_rows = list(cursor.fetchall())

    player_by_short_hash: dict[str, tuple[Any, str]] = {}
    for player_id, upstream_player_id in player_rows:
        full_hash = hashlib.sha256(str(upstream_player_id).encode("utf-8")).hexdigest()
        short_hash = full_hash[:16]
        if short_hash in player_by_short_hash:
            raise RuntimeError("A pseudonymous player-hash collision prevents safe analysis.")
        player_by_short_hash[short_hash] = (player_id, full_hash)
    chart_ids = {str(upstream_chart_id): chart_id for chart_id, upstream_chart_id in chart_rows}

    snapshot_players = {str(row["playerId"]) for row in snapshot["players"]}
    mapped_players = {str(upstream_player_id) for _, upstream_player_id in player_rows}
    snapshot_charts = {str(row["id"]) for row in snapshot["charts"]}
    if snapshot_players != mapped_players or snapshot_charts != set(chart_ids):
        raise RuntimeError("Canonical snapshot rows changed while their identifiers were resolved.")
    return DatabaseInput(mix_key, mix_id, snapshot, player_by_short_hash, chart_ids)


def _validate_output(output: AnalysisOutput) -> None:
    spec = resolve_mix(output.database_input.mix_key)
    payload = output.payload
    if not isinstance(payload.get("summary"), Mapping):
        raise ValueError("Analysis output has no summary object.")
    if not isinstance(payload.get("singles"), list) or not isinstance(payload.get("doubles"), list):
        raise ValueError("Analysis output is missing its chart result arrays.")
    payload_mix = payload.get("mix")
    if not isinstance(payload_mix, Mapping) or payload_mix.get("key") != spec.key:
        raise ValueError("Analysis output does not identify the selected mix.")
    if not payload.get("generatedAtUtc"):
        raise ValueError("Analysis output has no generation timestamp.")

    players = output.database_input.player_by_short_hash
    charts = output.database_input.chart_ids
    for row in [*output.baselines, *output.contributions]:
        if str(row.get("playerHash") or "") not in players:
            raise ValueError("An analysis player feature cannot be resolved to a canonical row.")
    for row in [*output.contributions, *output.chart_results]:
        if str(row.get("chartId") or "") not in charts:
            raise ValueError("An analysis chart result cannot be resolved to a canonical row.")
    _canonical_bytes(payload)
    _canonical_bytes(output.baselines)
    _canonical_bytes(output.contributions)
    _canonical_bytes(output.chart_results)


def _analyze(database_input: DatabaseInput, bootstrap_samples: int) -> AnalysisOutput:
    started_at = datetime.now(timezone.utc)
    config = AnalysisConfig(
        mix=database_input.mix_key,
        bootstrap_samples=bootstrap_samples,
    )
    snapshot = database_input.snapshot
    chart_frame, baseline_frame, summary, contribution_frame = analyze_snapshot(
        snapshot["players"], snapshot["charts"], snapshot["scores"], config
    )
    payload = build_web_payload(chart_frame, summary)
    output = AnalysisOutput(
        database_input=database_input,
        config=config,
        started_at=started_at,
        payload=payload,
        baselines=_frame_records(baseline_frame),
        contributions=_frame_records(contribution_frame),
        chart_results=_frame_records(chart_frame),
        source_hash=_sha256(snapshot),
        output_hash=_sha256(payload),
    )
    _validate_output(output)
    return output


def _methodology(output: AnalysisOutput) -> dict[str, Any]:
    configuration = asdict(output.config)
    code_hash = _file_hash(PROJECT_ROOT / "piu_misgrade_analyzer.py")
    if code_hash is None:  # pragma: no cover - invalid installation
        raise RuntimeError("The analyzer source file is unavailable.")
    dependency_hash = _file_hash(PROJECT_ROOT / "uv.lock")
    identity = _sha256(
        {
            "configuration": configuration,
            "dependencyHash": dependency_hash,
        }
    )
    return {
        "methodology_key": f"{METHODOLOGY_FAMILY}:{identity[:16]}",
        "script_version": SCRIPT_VERSION,
        "code_hash": code_hash,
        "dependency_hash": dependency_hash,
        "configuration": configuration,
        "random_seed": output.config.random_seed,
    }


def _persist_analysis(connection: Any, output: AnalysisOutput) -> Any:
    """Persist a validated immutable run and all typed facts in one short transaction."""
    from psycopg.types.json import Jsonb

    methodology = _methodology(output)
    summary = dict(output.payload["summary"])
    generated_at = str(output.payload["generatedAtUtc"])
    run_key = "local-analysis:{mix}:{digest}".format(
        mix=output.database_input.mix_key,
        digest=_sha256(
            {
                "sourceHash": output.source_hash,
                "outputHash": output.output_hash,
                "generatedAtUtc": generated_at,
                "methodology": methodology,
            }
        ),
    )
    with connection.transaction(), connection.cursor() as cursor:
        cursor.execute(
            """
            insert into pumbility.methodologies (
                methodology_key, script_version, code_hash, dependency_hash,
                configuration, random_seed
            ) values (%s, %s, %s, %s, %s, %s)
            on conflict (methodology_key, script_version, code_hash) do nothing
            returning id
            """,
            (
                methodology["methodology_key"],
                methodology["script_version"],
                methodology["code_hash"],
                methodology["dependency_hash"],
                Jsonb(methodology["configuration"]),
                methodology["random_seed"],
            ),
        )
        methodology_row = cursor.fetchone()
        if methodology_row is None:
            cursor.execute(
                """
                select id, dependency_hash, configuration, random_seed
                from pumbility.methodologies
                where methodology_key = %s and script_version = %s and code_hash = %s
                """,
                (
                    methodology["methodology_key"],
                    methodology["script_version"],
                    methodology["code_hash"],
                ),
            )
            existing = cursor.fetchone()
            if existing is None or (
                existing[1] != methodology["dependency_hash"]
                or dict(existing[2]) != methodology["configuration"]
                or existing[3] != methodology["random_seed"]
            ):
                raise RuntimeError("An immutable methodology identity has conflicting metadata.")
            methodology_id = existing[0]
        else:
            methodology_id = methodology_row[0]

        cursor.execute(
            """
            insert into pumbility.analysis_runs (
                run_key, mix_id, methodology_id, status, generated_at, source_hash,
                summary, input_hash, output_hash, coverage, metrics,
                started_at, completed_at, validated_at
            ) values (
                %s, %s, %s, 'shadow', %s::timestamptz, %s, %s, %s, %s, %s, %s,
                %s, %s::timestamptz, %s::timestamptz
            ) returning id
            """,
            (
                run_key,
                output.database_input.mix_id,
                methodology_id,
                generated_at,
                output.source_hash,
                Jsonb(summary),
                output.source_hash,
                output.output_hash,
                Jsonb(dict(summary.get("coverage") or {})),
                Jsonb(dict(summary.get("modes") or {})),
                output.started_at,
                generated_at,
                generated_at,
            ),
        )
        run_id = cursor.fetchone()[0]

        mode_rows = []
        for mode in ("Singles", "Doubles"):
            metrics = dict((summary.get("modes") or {}).get(mode.lower()) or {})
            mode_rows.append(
                (
                    run_id,
                    mode,
                    Jsonb(metrics),
                    int(metrics.get("eligiblePlayers") or 0),
                    int(metrics.get("catalogCharts") or 0),
                    Jsonb(dict(metrics.get("calibration") or {})),
                    Jsonb(dict(metrics.get("shrinkage") or {})),
                    Jsonb({"folders": dict(metrics.get("folders") or {})}),
                )
            )
        cursor.executemany(
            """
            insert into pumbility.analysis_mode_results (
                analysis_run_id, mode, metrics, eligible_player_count, chart_count,
                calibration, shrinkage, coverage
            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            mode_rows,
        )

        player_rows = []
        for row in output.baselines:
            player_id, full_hash = output.database_input.player_by_short_hash[row["playerHash"]]
            player_rows.append(
                (
                    run_id,
                    player_id,
                    full_hash,
                    row["mode"],
                    int(row["validScoreCount"]),
                    _nullable_number(row.get("baselinePumbility")),
                    _nullable_number(row.get("baselineStd")),
                    _nullable_number(row.get("baselineMin")),
                    _nullable_number(row.get("baselineMax")),
                    int(row.get("baselineCount") or 0),
                    Jsonb(row),
                )
            )
        cursor.executemany(
            """
            insert into pumbility.player_mode_features (
                analysis_run_id, player_id, player_hash, mode, valid_score_count,
                baseline_pumbility, baseline_std, baseline_min, baseline_max,
                baseline_count, payload
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            player_rows,
        )

        contribution_rows = []
        for row in output.contributions:
            player_id, full_hash = output.database_input.player_by_short_hash[row["playerHash"]]
            contribution_rows.append(
                (
                    run_id,
                    output.database_input.chart_ids[str(row["chartId"])],
                    player_id,
                    full_hash,
                    row["mode"],
                    float(row["pumbility"]),
                    float(row["baselinePumbility"]),
                    float(row["residualPb"]),
                    Jsonb(row),
                    _nullable_number(row.get("playerRank"), integer=True),
                    bool(row.get("selectedByPumbility")),
                    bool(row.get("selectedByRecency")),
                    bool(row.get("selectedByTop100Fallback")),
                    row.get("recordedAt") or None,
                )
            )
        cursor.executemany(
            """
            insert into pumbility.chart_contributions (
                analysis_run_id, chart_id, player_id, player_hash, mode,
                pumbility, baseline_pumbility, residual_pb, payload, rank_index,
                selected_top, selected_recent, selected_fallback, recorded_at
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            contribution_rows,
        )

        chart_rows = []
        for row in output.chart_results:
            chart_rows.append(
                (
                    run_id,
                    output.database_input.chart_ids[str(row["chartId"])],
                    row["mode"],
                    _nullable_number(row.get("estimatedDifficulty")),
                    _nullable_number(row.get("averageDifficulty")),
                    _nullable_number(row.get("difficultyDelta")),
                    _nullable_number(row.get("difficultyDeltaCi95Low")),
                    _nullable_number(row.get("difficultyDeltaCi95High")),
                    _nullable_number(row.get("difficultyCi95Low")),
                    _nullable_number(row.get("difficultyCi95High")),
                    int(row.get("nContributors") or 0),
                    int(row.get("nPlayersScored") or 0),
                    row.get("evidenceStatus"),
                    _nullable_number(row.get("modeRank"), integer=True),
                    _nullable_number(row.get("levelRank"), integer=True),
                    Jsonb(row),
                )
            )
        cursor.executemany(
            """
            insert into pumbility.chart_results (
                analysis_run_id, chart_id, mode, estimated_difficulty,
                average_difficulty, difficulty_delta, difficulty_delta_ci95_low,
                difficulty_delta_ci95_high, difficulty_ci95_low, difficulty_ci95_high,
                n_contributors, n_players_scored, evidence_status, mode_rank,
                level_rank, payload
            ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            chart_rows,
        )
    return run_id


def _publish_latest(store: PumbilityArtifactStore, outputs: Sequence[AnalysisOutput]) -> None:
    """Atomically promote every selected mix's compatibility pointer."""
    store.put_json_bundle(
        {
            latest_blob_path(output.database_input.mix_key): output.payload
            for output in outputs
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", default="PUMBILITY_DATABASE_URL")
    parser.add_argument(
        "--mix",
        action="append",
        choices=sorted(MIX_SPECS),
        help="Mix to analyze; repeat for both. Defaults to both mixes.",
    )
    parser.add_argument(
        "--bootstrap-samples",
        type=int,
        default=DEFAULT_BOOTSTRAP_SAMPLES,
        help="Bootstrap samples per chart (production-equivalent default: 500).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.bootstrap_samples < 0:
        raise ValueError("--bootstrap-samples must be nonnegative.")
    database_url = os.getenv(args.database_url_env, "").strip()
    if not database_url:
        raise RuntimeError(f"{args.database_url_env} is not configured.")
    require_loopback_database_url(database_url)
    mixes = args.mix or sorted(MIX_SPECS)

    import psycopg

    # Finish all private reads before the CPU-heavy analysis begins.
    with psycopg.connect(database_url) as read_connection:
        with read_connection.cursor() as cursor:
            _assert_schema(cursor)
        inputs = [_read_database_input(read_connection, mix) for mix in mixes]
    outputs = [_analyze(database_input, args.bootstrap_samples) for database_input in inputs]

    # Validate every selected mix before any typed run or latest key is changed.
    for output in outputs:
        _validate_output(output)
    for output in outputs:
        with psycopg.connect(database_url) as write_connection:
            _persist_analysis(write_connection, output)

    store = PumbilityArtifactStore(database_url=database_url)
    _publish_latest(store, outputs)
    print(
        json.dumps(
            {
                "status": "completed",
                "mixes": {
                    output.database_input.mix_key: {
                        "charts": len(output.chart_results),
                        "playerModeFeatures": len(output.baselines),
                        "contributions": len(output.contributions),
                    }
                    for output in outputs
                },
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Pumbility analysis failed safely; private database details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
