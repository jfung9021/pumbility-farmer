"""Private, pinned official Scoring evidence; independent of Clearing snapshots.

Captures retain game tags for deterministic identity resolution. Never publish a
capture or a loaded manifest: only aggregate merge/model outputs are public.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlencode
from uuid import UUID

SCORING_SOURCE_SCHEMA_VERSION = 1
SCORING_MERGE_POLICY = "full-tag-bidirectional-raw-max-v1"
SCORING_METHOD_VERSION = "official-cross-level-raw-v1"
SCORING_CALIBRATION_VERSION = "median-exponential-segments-v1"
MIXES = ("Phoenix", "Phoenix2")
BOARD_CAPS = {"Phoenix": 100, "Phoenix2": 300}
SOURCE = "piuscores-official"
DEFAULT_OFFICIAL_SCORING_MANIFEST_PATH = (
    Path(__file__).resolve().parent / ".local-data/piu-scores/official/scoring/manifest.json"
)
_STATES = {"available", "empty", "absent-from-catalog", "not-mirrored", "not-captured"}
PHOENIX_ARCHIVE_NAME = "phoenix-static.json"


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode("utf-8")


def _sha(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def canonical_scoring_manifest_sha256(manifest: Mapping[str, Any]) -> str:
    """Hash the persisted contract, ignoring convenience fields added by loading."""
    contract = {key: value for key, value in manifest.items() if not key.startswith("_")}
    encoded = json.dumps(contract, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Official scoring timestamp must be timezone-aware.")
    try:
        # API timestamps use .NET's seven fractional digits; Python 3.9 only
        # accepts three/six. Keep the original timestamp in all hashed evidence.
        normalized = re.sub(r"(\.\d{6})\d+(?=[+-]|$)", r"\1", value.replace("Z", "+00:00"))
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        raise ValueError("Official scoring timestamp must be timezone-aware.") from None
    if parsed.tzinfo is None:
        raise ValueError("Official scoring timestamp must be timezone-aware.")
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Capture clock must be timezone-aware.")
    return value.astimezone(timezone.utc).isoformat()


def _integer(value: Any, label: str, minimum: int, maximum: int | None = None) -> int:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"Invalid official scoring {label}.")
    return value


def _uuid(value: Any) -> str:
    try:
        if not isinstance(value, str):
            raise ValueError
        return str(UUID(value))
    except ValueError:
        raise ValueError("Invalid official scoring chart UUID.") from None


def _catalog(charts: Any, mix: str) -> dict[str, dict[str, Any]]:
    if not isinstance(charts, list):
        raise ValueError("Official scoring catalog must be an array.")
    result = {}
    for chart in charts:
        if not isinstance(chart, Mapping):
            raise ValueError("Invalid official scoring catalog row.")
        cid = _uuid(chart.get("id", chart.get("chartId")))
        if cid in result:
            raise ValueError("Duplicate official scoring chart UUID.")
        if chart.get("mix", mix) != mix:
            raise ValueError("Official scoring catalog mixes differ.")
        result[cid] = dict(chart)
    return result


def scoring_target_charts(charts: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    result = {}
    for cid, chart in _catalog(list(charts), "Phoenix2").items():
        level = chart.get("level")
        if chart.get("type") in ("Single", "Double") and type(level) in (int, float) and math.isfinite(level) and level >= 20:
            result[cid] = chart
    return dict(sorted(result.items()))


def _state(cid: str, mix: str, status: str, fetched_at: str) -> dict[str, Any]:
    return {"chartId": cid, "mix": mix, "status": status, "fetchedAtUtc": fetched_at,
            "asOf": None, "rawRowCount": 0, "maxRank": 0, "cutoffScore": None,
            "possiblyTruncated": False, "entries": []}


def normalize_scoring_board(cid: str, mix: str, payload: Mapping[str, Any], fetched_at: str) -> dict[str, Any]:
    if mix not in MIXES or not isinstance(payload, Mapping) or not isinstance(payload.get("data"), list):
        raise ValueError("Invalid official scoring board payload.")
    _timestamp(payload.get("asOf"))
    _timestamp(fetched_at)
    entries = []
    for raw in payload["data"]:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("player"), Mapping):
            raise ValueError("Official scoring row lacks a player.")
        player = raw["player"]
        if player.get("isSupplemented") is not False:
            raise ValueError("Official scoring row is supplemented or lacks provenance.")
        tag = player.get("gameTag")
        if tag is not None and not isinstance(tag, str):
            raise ValueError("Invalid official scoring game tag.")
        entries.append({"playerId": _integer(player.get("playerId"), "player ID", 1),
                        "gameTag": tag, "isSupplemented": False,
                        "score": _integer(raw.get("score"), "score", 0, 1_000_000),
                        "place": _integer(raw.get("place"), "place", 1)})
    result = _state(_uuid(cid), mix, "available" if entries else "empty", fetched_at)
    result.update(asOf=payload["asOf"], entries=entries, rawRowCount=len(entries),
                  maxRank=max((r["place"] for r in entries), default=0),
                  cutoffScore=min((r["score"] for r in entries), default=None))
    result["possiblyTruncated"] = result["rawRowCount"] >= BOARD_CAPS[mix] or result["maxRank"] >= BOARD_CAPS[mix]
    return result


def _validate_capture(capture: Mapping[str, Any], current: Mapping[str, Any], *, complete: bool = True) -> dict[str, Any]:
    if not isinstance(capture, Mapping) or capture.get("schemaVersion") != SCORING_SOURCE_SCHEMA_VERSION or capture.get("source") != SOURCE:
        raise ValueError("Incompatible official scoring capture.")
    mix = capture.get("mix")
    if mix not in MIXES:
        raise ValueError("Invalid official scoring capture mix.")
    _timestamp(capture.get("fetchedAtUtc"))
    as_of = _timestamp(capture.get("asOf"))
    catalog = _catalog(capture.get("catalog"), mix)
    if capture.get("catalogSha256") != _sha(capture["catalog"]):
        raise ValueError("Official scoring catalog hash differs.")
    if capture.get("targetChartIds") != sorted(current):
        raise ValueError("Official scoring capture target scope differs.")
    boards = capture.get("boards")
    if not isinstance(boards, dict) or not set(boards).issubset(current) or (complete and set(boards) != set(current)):
        raise ValueError("Official scoring capture is incomplete or outside target scope.")
    for cid, board in boards.items():
        if not isinstance(board, Mapping) or board.get("chartId") != cid or board.get("mix") != mix or board.get("status") not in _STATES:
            raise ValueError("Invalid official scoring board state.")
        _timestamp(board.get("fetchedAtUtc"))
        present = cid in catalog
        if present and catalog[cid].get("type") != current[cid].get("type"):
            raise ValueError("Official scoring chart mode changed across catalogs.")
        if (board["status"] == "absent-from-catalog") != (not present):
            raise ValueError("Official scoring board catalog membership is inconsistent.")
        if board["status"] == "not-captured" and mix != "Phoenix":
            raise ValueError("Only frozen Phoenix evidence may be outside captured scope.")
        if board["status"] in ("available", "empty"):
            if board.get("asOf") != capture["asOf"]:
                raise ValueError("Official scoring capture mixes source dates.")
            entries = board.get("entries")
            if not isinstance(entries, list):
                raise ValueError("Invalid official scoring entries.")
            payload = {"asOf": board["asOf"], "data": [
                {"score": r.get("score"), "place": r.get("place"), "player": r} if isinstance(r, Mapping) else {} for r in entries]}
            normalized = normalize_scoring_board(cid, mix, payload, board["fetchedAtUtc"])
            if normalized != board:
                raise ValueError("Official scoring board metadata is inconsistent.")
        elif board != _state(cid, mix, board["status"], board["fetchedAtUtc"]):
            raise ValueError("Unavailable official scoring board has unexpected entries or metadata.")
    return dict(capture)


def _atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(_json_bytes(value))
    os.replace(temporary, path)


def _capture_object(mix: str, catalog: list, current: Mapping[str, Any], boards: dict, as_of: str, fetched_at: str) -> dict[str, Any]:
    return {"schemaVersion": SCORING_SOURCE_SCHEMA_VERSION, "source": SOURCE, "mix": mix,
            "catalog": catalog, "catalogSha256": _sha(catalog), "targetChartIds": sorted(current),
            "asOf": as_of, "fetchedAtUtc": fetched_at, "boards": boards}


def validate_phoenix_archive(archive: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the private, permanent original source independently of today's scope."""
    if not isinstance(archive, Mapping) or archive.get("schemaVersion") != 1:
        raise ValueError("Invalid frozen Phoenix archive.")
    capture = archive.get("capture")
    if (not isinstance(capture, Mapping) or capture.get("mix") != "Phoenix"
            or archive.get("sha256") != _sha(capture)):
        raise ValueError("Frozen Phoenix archive content hash differs.")
    ids = capture.get("targetChartIds")
    if not isinstance(ids, list) or ids != sorted({_uuid(cid) for cid in ids}):
        raise ValueError("Invalid frozen Phoenix archive scope.")
    catalog = _catalog(capture.get("catalog"), "Phoenix")
    return _validate_capture(capture, {cid: catalog.get(cid, {}) for cid in ids})


def _freeze_phoenix(path: Path, capture: Mapping[str, Any]) -> dict[str, Any]:
    archive = {"schemaVersion": 1, "sha256": _sha(capture), "capture": dict(capture)}
    validated = validate_phoenix_archive(archive)
    _atomic_json(path.parent / PHOENIX_ARCHIVE_NAME, archive)
    return validated


def frozen_phoenix_source(path: Path) -> dict[str, Any] | None:
    """Adopt a complete existing capture once; never probe or replace an archive."""
    path = Path(path)
    archive_path = path.parent / PHOENIX_ARCHIVE_NAME
    if archive_path.exists():
        return validate_phoenix_archive(json.loads(archive_path.read_bytes()))
    if path.exists():
        return _freeze_phoenix(path, load_official_scoring_manifest(path)["_captures"]["Phoenix"])
    return None


def fetch_official_scoring_catalogs(client: Any, path: Path) -> dict[str, list]:
    frozen = frozen_phoenix_source(path)
    return {
        "Phoenix": frozen["catalog"] if frozen is not None else client.fetch_page_collection("api/v2/charts?mix=Phoenix&limit=500"),
        "Phoenix2": client.fetch_page_collection("api/v2/charts?mix=Phoenix2&limit=500"),
    }


def _project_frozen_phoenix(frozen: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    if frozen["targetChartIds"] == sorted(current):
        return _validate_capture(frozen, current)
    catalog = _catalog(frozen["catalog"], "Phoenix")
    boards = {cid: frozen["boards"].get(cid) or _state(
        cid, "Phoenix", "not-captured" if cid in catalog else "absent-from-catalog", frozen["fetchedAtUtc"])
        for cid in current}
    projected = {**frozen, "targetChartIds": sorted(current), "boards": boards,
                 "frozenSourceSha256": _sha(frozen)}
    return _validate_capture(projected, current)


def publish_official_scoring_manifest(captures: Mapping[str, Mapping[str, Any]], path: Path = DEFAULT_OFFICIAL_SCORING_MANIFEST_PATH) -> dict[str, Any]:
    """Publish private content-addressed evidence, then atomically replace its pointer."""
    path = Path(path)
    if set(captures) != set(MIXES):
        raise ValueError("Both official scoring mixes are required.")
    current = scoring_target_charts(captures["Phoenix2"]["catalog"])
    refs = {}
    for mix in MIXES:
        cap = _validate_capture(captures[mix], current)
        digest = _sha(cap)
        relative = f"captures/{mix}-{digest}.json"
        _atomic_json(path.parent / relative, cap)
        refs[mix] = {"path": relative, "sha256": digest, "catalogSha256": cap["catalogSha256"],
                     "asOf": cap["asOf"], "fetchedAtUtc": cap["fetchedAtUtc"]}
    manifest = {"schemaVersion": SCORING_SOURCE_SCHEMA_VERSION, "source": SOURCE,
                "mergePolicyVersion": SCORING_MERGE_POLICY, "scoringMethodVersion": SCORING_METHOD_VERSION,
                "calibrationVersion": SCORING_CALIBRATION_VERSION,
                "scope": {"modes": ["Single", "Double"], "minimumCurrentLevel": 20}, "captures": refs}
    _atomic_json(path, manifest)
    return load_official_scoring_manifest(path)


def load_official_scoring_manifest(path: Path = DEFAULT_OFFICIAL_SCORING_MANIFEST_PATH, current_charts: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    path = Path(path)
    manifest = json.loads(path.read_bytes())

    def loader(relative: str) -> dict[str, Any]:
        source_path = (path.parent / relative).resolve()
        if path.parent.resolve() not in source_path.parents:
            raise ValueError("Official scoring capture must remain within its private manifest directory.")
        capture_bytes = source_path.read_bytes()
        ref = next(r for r in manifest["captures"].values() if r["path"] == relative)
        if hashlib.sha256(capture_bytes).hexdigest() != ref["sha256"]:
            raise ValueError("Official scoring capture content hash differs.")
        return json.loads(capture_bytes)

    return hydrate_official_scoring_manifest(manifest, loader, current_charts)


def refresh_hosted_official_scoring_sources(client: Any, store: Any, *, max_boards: int = 100) -> dict[str, Any]:
    """Capture live official boards inside the protected hosted environment.

    Write immutable captures before advancing the private manifest. A failed
    fetch or storage validation leaves the last complete manifest available.
    """
    from pumbility_contract import official_scoring_manifest_path, phoenix_scoring_archive_path

    key = official_scoring_manifest_path()
    prefix = key.rsplit("/", 1)[0]
    pending_key = f"{prefix}/refresh-pending.json"
    archive_key = phoenix_scoring_archive_path()
    class BatchComplete(Exception):
        pass
    class BoundedClient:
        def __init__(self):
            self.count = 0
        def _get_json(self, path):
            from analysis_budget import work_budget_available
            if self.count >= max_boards or not work_budget_available():
                raise BatchComplete()
            self.count += 1
            return client._get_json(path)
    if type(max_boards) is not int or max_boards < 1:
        raise ValueError("Official capture batch size must be positive.")
    with tempfile.TemporaryDirectory(prefix="piu-official-scoring-") as directory:
        path = Path(directory) / "manifest.json"
        previous = store.get_json(key)
        if previous is not None:
            loaded = hydrate_official_scoring_manifest(
                previous, lambda relative: store.get_json(f"{prefix}/{relative}"),
            )
            publish_official_scoring_manifest(loaded["_captures"], path)
        archive = store.get_json(archive_key)
        if archive is not None:
            validate_phoenix_archive(archive)
            _atomic_json(path.parent / PHOENIX_ARCHIVE_NAME, archive)
        def persist_archive():
            nonlocal archive
            # Existing archives are never overwritten, including partial/restart paths.
            local_archive = path.parent / PHOENIX_ARCHIVE_NAME
            if archive is None and local_archive.exists():
                payload = json.loads(local_archive.read_bytes())
                validate_phoenix_archive(payload)
                store.put_json(archive_key, payload)
                archive = payload
        # Adopt the complete published Phoenix capture before any upstream call.
        frozen_phoenix_source(path)
        persist_archive()
        pending = store.get_json(pending_key) or {}
        for mix in MIXES:
            if mix in pending:
                _atomic_json(path.parent / f"{mix}.pending.json", pending[mix])
        catalogs = fetch_official_scoring_catalogs(client, path)
        try:
            loaded = capture_official_scoring_sources(
                BoundedClient(), catalogs, path, checkpoint_interval=min(25, max_boards),
            )
        except BatchComplete:
            pending = {mix: json.loads((path.parent / f"{mix}.pending.json").read_bytes())
                       for mix in MIXES if (path.parent / f"{mix}.pending.json").exists()}
            store.put_json(pending_key, pending)
            return {"outcome": "official-sources-progress",
                    "capturedBoards": {mix: len(cap["boards"]) for mix, cap in pending.items()},
                    "targetCharts": len(scoring_target_charts(catalogs["Phoenix2"])),
                    "progressIdentity": _sha({mix: {
                        "asOf": cap["asOf"], "catalogSha256": cap["catalogSha256"],
                        "targetChartIds": cap["targetChartIds"], "completed": len(cap["boards"]),
                    } for mix, cap in pending.items()})}
        finally:
            persist_archive()
        manifest = {k: v for k, v in loaded.items() if not k.startswith("_")}
        for mix, ref in manifest["captures"].items():
            store.put_json(f"{prefix}/{ref['path']}", loaded["_captures"][mix])
        hydrate_official_scoring_manifest(
            manifest, lambda relative: store.get_json(f"{prefix}/{relative}"),
        )
        store.put_json_bundle({key: manifest, pending_key: {}})
    return {
        "sourceDates": {mix: ref["asOf"] for mix, ref in manifest["captures"].items()},
        "boardCounts": {mix: len(loaded["_captures"][mix]["boards"]) for mix in MIXES},
        "manifestSha256": canonical_scoring_manifest_sha256(manifest),
    }


def hydrate_official_scoring_manifest(manifest: Mapping[str, Any], loader: Callable[[str], Mapping[str, Any]],
                                      current_charts: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Validate a persisted manifest using a private blob/disk JSON loader.

    ``loader`` receives a relative reference such as ``captures/Phoenix-<sha>.json``.
    The caller resolves it under that manifest's private directory/key prefix.
    """
    if (not isinstance(manifest, dict) or manifest.get("schemaVersion") != SCORING_SOURCE_SCHEMA_VERSION
            or manifest.get("source") != SOURCE or manifest.get("mergePolicyVersion") != SCORING_MERGE_POLICY
            or manifest.get("scoringMethodVersion") != SCORING_METHOD_VERSION
            or manifest.get("calibrationVersion") != SCORING_CALIBRATION_VERSION
            or manifest.get("scope") != {"modes": ["Single", "Double"], "minimumCurrentLevel": 20}
            or not isinstance(manifest.get("captures"), dict) or set(manifest["captures"]) != set(MIXES)):
        raise ValueError("Incompatible official scoring manifest.")
    captures = {}
    for mix in MIXES:
        ref = manifest["captures"][mix]
        if (not isinstance(ref, dict) or not isinstance(ref.get("sha256"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])
                or ref.get("path") != f"captures/{mix}-{ref['sha256']}.json"):
            raise ValueError("Invalid official scoring capture reference.")
        capture = loader(ref["path"])
        if not isinstance(capture, Mapping) or _sha(capture) != ref["sha256"]:
            raise ValueError("Official scoring capture content hash differs.")
        if capture.get("mix") != mix or any(capture.get(k) != ref.get(k) for k in ("asOf", "fetchedAtUtc", "catalogSha256")):
            raise ValueError("Official scoring manifest provenance differs from capture.")
        captures[mix] = capture
    current = scoring_target_charts(captures["Phoenix2"]["catalog"])
    if current_charts is not None:
        requested = scoring_target_charts(current_charts)
        incoming_catalog = _catalog(list(current_charts), "Phoenix2")
        pinned_catalog = _catalog(captures["Phoenix2"]["catalog"], "Phoenix2")
        if not set(requested).issubset(current) or any(
                any(incoming_catalog[c].get(k) != pinned_catalog[c].get(k) for k in ("type", "level", "noteCount"))
                for c in set(incoming_catalog) & set(pinned_catalog)):
            raise ValueError("Official scoring manifest does not match the current chart catalog.")
    for cap in captures.values():
        _validate_capture(cap, current)
    return {**manifest, "_captures": captures, "_currentCharts": current,
            "_manifestSha256": canonical_scoring_manifest_sha256(manifest)}


def import_research_scoring_sources(directory: Path, path: Path = DEFAULT_OFFICIAL_SCORING_MANIFEST_PATH) -> dict[str, Any]:
    """Validate and migrate a complete research capture without network access."""
    directory = Path(directory)
    catalogs = {mix: json.loads((directory / f"{mix}-catalog.json").read_text(encoding="utf-8")) for mix in MIXES}
    current = scoring_target_charts(catalogs["Phoenix2"])
    captures = {}
    for mix in MIXES:
        source_catalog = _catalog(catalogs[mix], mix)
        boards = {}
        dates = set()
        latest = datetime.fromtimestamp((directory / f"{mix}-catalog.json").stat().st_mtime, timezone.utc)
        for cid in current:
            board_path = directory / mix / f"{cid}.json"
            if cid not in source_catalog:
                if board_path.exists():
                    raise ValueError("Research capture has a board absent from its source catalog.")
                boards[cid] = _state(cid, mix, "absent-from-catalog", _iso(latest))
                continue
            # Missing files and invalid rows abort before touching the current manifest.
            saved = json.loads(board_path.read_text(encoding="utf-8"))
            fetched = datetime.fromtimestamp(board_path.stat().st_mtime, timezone.utc)
            latest = max(latest, fetched)
            if saved.get("chartId") != cid or saved.get("mix") != mix:
                raise ValueError("Research board identity differs from its file/catalog.")
            if saved.get("unavailable") == "not-mirrored":
                if saved.get("entries") or saved.get("asOf"):
                    raise ValueError("Research missing-board marker contains score data.")
                boards[cid] = _state(cid, mix, "not-mirrored", _iso(fetched))
            elif "unavailable" in saved:
                raise ValueError("Unrecognized research board failure cannot be imported.")
            else:
                rows = saved.get("entries")
                if not isinstance(rows, list):
                    raise ValueError("Research board lacks entries.")
                payload = {"asOf": saved.get("asOf"), "data": [
                    {"score": r.get("score"), "place": r.get("place"), "player": r} if isinstance(r, Mapping) else {} for r in rows]}
                boards[cid] = normalize_scoring_board(cid, mix, payload, _iso(fetched))
                dates.add(boards[cid]["asOf"])
        if len(dates) != 1:
            raise ValueError("Research capture must have one source date per mix.")
        capture = _capture_object(mix, catalogs[mix], current, boards, sorted(dates)[0], _iso(latest))
        capture["captureProvenance"] = {"method": "research-import", "fetchTimeBasis": "source-file-mtime"}
        captures[mix] = capture
    return publish_official_scoring_manifest(captures, path)


def capture_official_scoring_sources(client: Any, catalogs: Mapping[str, list], path: Path = DEFAULT_OFFICIAL_SCORING_MANIFEST_PATH,
                                     *, restart: bool = False, now: Callable[[], datetime] = _utc_now,
                                     progress: Callable[[str, int, int], None] | None = None,
                                     checkpoint_interval: int = 1) -> dict[str, Any]:
    """Freeze Phoenix once; probe/resume only the live Phoenix2 snapshot thereafter.

    Restart discards partial live work but cannot bypass the permanent archive.
    Failed fetches never become empty boards or replace the complete manifest.
    """
    path = Path(path)
    current = scoring_target_charts(catalogs["Phoenix2"])
    if not current:
        raise ValueError("No eligible official scoring charts.")
    frozen = frozen_phoenix_source(path)
    captures = {}
    for mix in MIXES:
        if mix == "Phoenix" and frozen is not None:
            captures[mix] = _project_frozen_phoenix(frozen, current)
            _atomic_json(path.parent / f"{mix}.pending.json", captures[mix])
            if progress:
                progress(mix, len(current), len(current))
            continue
        source_catalog = _catalog(catalogs[mix], mix)
        pending_path = path.parent / f"{mix}.pending.json"
        prior = None
        if not restart and pending_path.exists():
            candidate = json.loads(pending_path.read_text(encoding="utf-8"))
            if candidate.get("catalogSha256") == _sha(catalogs[mix]) and candidate.get("targetChartIds") == sorted(current):
                prior = _validate_capture(candidate, current, complete=False)
        boards = {}
        as_of = None
        # A date-bearing live board is necessary before reusing any cached state.
        for cid in current:
            if cid not in source_catalog:
                boards[cid] = _state(cid, mix, "absent-from-catalog", _iso(now()))
                continue
            if as_of is not None and prior and cid in prior["boards"]:
                board = prior["boards"][cid]
            else:
                query = urlencode({"mix": mix, "supplemented": "false"})
                try:
                    payload = client._get_json(f"api/v2/official/charts/{cid}/board?{query}")
                    board = normalize_scoring_board(cid, mix, payload, _iso(now()))
                except Exception as exc:
                    # The existing client's safe ApiError reports "HTTP 404".
                    if not re.search(r"\bHTTP 404\b", str(exc)):
                        raise
                    board = _state(cid, mix, "not-mirrored", _iso(now()))
            if board["status"] in ("available", "empty"):
                if as_of is None:
                    as_of = board["asOf"]
                    if prior and prior["asOf"] != as_of:
                        prior = None
                elif board["asOf"] != as_of:
                    raise ValueError("Official scoring boards changed source dates during capture.")
            boards[cid] = board
            if as_of is not None and (len(boards) % checkpoint_interval == 0 or len(boards) == len(current)):
                capture = _capture_object(mix, catalogs[mix], current, dict(boards), as_of, _iso(now()))
                capture["captureProvenance"] = {"method": "authenticated-api"}
                _atomic_json(pending_path, capture)
            if progress:
                progress(mix, len(boards), len(current))
        if as_of is None:
            raise ValueError("Cannot pin an official scoring source date without a date-bearing board.")
        captures[mix] = _capture_object(mix, catalogs[mix], current, boards, as_of, _iso(now()))
        captures[mix]["captureProvenance"] = {"method": "authenticated-api"}
        _validate_capture(captures[mix], current)
        if mix == "Phoenix":
            _freeze_phoenix(path, captures[mix])
    loaded = publish_official_scoring_manifest(captures, path)
    for mix in MIXES:
        (path.parent / f"{mix}.pending.json").unlink(missing_ok=True)
    return loaded
