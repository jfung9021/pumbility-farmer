"""Build the current schema-3 recommendation model from local Supabase rows."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from piu_recommendations import (  # noqa: E402
    build_combined_chart_results,
    build_combined_tier_payload,
    combined_tier_blob_path,
    recommendation_blob_path,
    recommendation_generation_key,
)
from pumbility_store import (  # noqa: E402
    PumbilityArtifactStore,
    _assert_schema,
    require_loopback_database_url,
)
from recommendation_refresh import (  # noqa: E402
    build_recommendation_model_artifacts,
    publish_recommendation_model_artifacts,
)
from scripts.reconcile_pumbility_supabase import _database_snapshot  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url-env", default="PUMBILITY_DATABASE_URL")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    database_url = os.getenv(args.database_url_env, "").strip()
    if not database_url:
        raise RuntimeError(f"{args.database_url_env} is not configured.")
    require_loopback_database_url(database_url)
    import psycopg

    with psycopg.connect(database_url) as connection:
        with connection.cursor() as cursor:
            _assert_schema(cursor)
        phoenix1 = _database_snapshot(connection, "phoenix1")
        phoenix2 = _database_snapshot(connection, "phoenix2")
    combined_charts, slopes, metadata = build_combined_chart_results(phoenix1, phoenix2)
    combined_payload = build_combined_tier_payload(combined_charts, metadata)
    generated_at = str(combined_payload["generatedAtUtc"])
    generation_key = recommendation_generation_key(generated_at)
    index, model, score_bytes, p1_shards, p2_shards = build_recommendation_model_artifacts(
        phoenix1,
        phoenix2,
        combined_charts=combined_charts,
        phoenix2_slopes=slopes,
        generation_key=generation_key,
        generated_at_utc=generated_at,
    )
    store = PumbilityArtifactStore(database_url=database_url)
    publish_recommendation_model_artifacts(
        store,
        index=index,
        model=model,
        score_model_bytes=score_bytes,
        phoenix1_shards=p1_shards,
        phoenix2_shards=p2_shards,
        index_path=recommendation_blob_path(),
        publish_index=False,
    )
    store.put_json_bundle(
        {
            combined_tier_blob_path(): combined_payload,
            recommendation_blob_path(): index,
        }
    )
    print(
        f"Published local recommendation generation {generation_key} for "
        f"{len(index['players']):,} players across {len(p1_shards):,} input shards."
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Pumbility model build failed safely; private database details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
