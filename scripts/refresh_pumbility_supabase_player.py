"""Build one cached player recommendation from the imported local database state."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from piu_recommendations import recommendation_blob_path  # noqa: E402
from pumbility_store import (  # noqa: E402
    PumbilityArtifactStore,
    _assert_schema,
    require_loopback_database_url,
)
from recommendation_refresh import (  # noqa: E402
    find_player_metadata,
    refresh_player_recommendations,
)
from scripts.reconcile_pumbility_supabase import _typed_score_payload  # noqa: E402


class LocalDatabaseScoreClient:
    """The selected-player refresh client backed by already imported local rows."""

    def __init__(self, database_url: str, player_id: str) -> None:
        self.database_url = database_url
        self.player_id = player_id

    def fetch_page_collection(
        self, initial_path: str, params: Mapping[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        expected = f"api/v2/players/{self.player_id}/scores"
        if initial_path != expected or dict(params or {}) != {"mix": "Phoenix2", "limit": 100}:
            raise ValueError("The offline player refresh requested an unexpected upstream shape.")
        import psycopg

        with psycopg.connect(self.database_url, prepare_threshold=None) as connection:
            with connection.cursor() as cursor:
                _assert_schema(cursor)
                cursor.execute(
                    """
                    select p.upstream_player_id, c.upstream_chart_id, sr.pumbility,
                           sr.score, sr.letter_grade, sr.plate, sr.recorded_at_raw,
                           sr.is_broken
                    from pumbility.score_revisions sr
                    join pumbility.players p on p.id = sr.player_id
                    join pumbility.mixes m on m.id = sr.mix_id
                    join pumbility.charts c on c.id = sr.chart_id
                    where p.upstream_player_id = %s
                      and m.mix_key = 'phoenix2'
                      and sr.valid_to is null
                    order by c.upstream_chart_id
                    """,
                    (self.player_id,),
                )
                return [_typed_score_payload(row) for row in cursor.fetchall()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", default="PUMBILITY_DATABASE_URL")
    parser.add_argument("--player-key", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv(args.database_url_env, "").strip()
    if not database_url:
        raise RuntimeError(f"{args.database_url_env} is not configured.")
    require_loopback_database_url(database_url)
    store = PumbilityArtifactStore(database_url=database_url)
    index = store.get_json(recommendation_blob_path())
    if index is None:
        raise RuntimeError("Build the local recommendation model before refreshing a player.")
    metadata = find_player_metadata(index, args.player_key)
    if metadata is None:
        raise ValueError("The requested public player key is not in the local recommendation index.")
    internal_id = str(metadata["internalPlayerId"])
    result = refresh_player_recommendations(
        store,
        LocalDatabaseScoreClient(database_url, internal_id),
        index_path=recommendation_blob_path(),
        player_key=args.player_key,
    )
    print(
        json.dumps(
            {
                "status": "completed",
                "modelGenerationPresent": bool(result.get("modelGeneration")),
                "candidateCount": sum(
                    len(value.get("candidates", []))
                    for value in (result.get("player") or {}).get("modes", {}).values()
                    if isinstance(value, Mapping)
                ),
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
            "Pumbility player refresh failed safely; private details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
