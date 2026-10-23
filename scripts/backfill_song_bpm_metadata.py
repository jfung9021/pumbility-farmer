"""Prepare/apply a local metadata-only BPM backfill; default is a dry run."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bpm_metadata_migration import apply_migration, prepare_migration, stage_local_snapshot
from pumbility_store import _connect, require_loopback_database_url


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, help="Saved dry-run plan (required for database modes)")
    parser.add_argument("--apply", action="store_true", help="Apply the saved pinned plan to a loopback database")
    parser.add_argument("--database-url", default=None, help="Defaults to PUMBILITY_DATABASE_URL; loopback only")
    parser.add_argument("--prepared-artifacts", type=Path, help="Optional pointer mapping: payload, sha256, expectedRevision")
    parser.add_argument("--snapshot-dir", type=Path, help="Instead stage a local snapshot candidate")
    parser.add_argument("--candidate-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.snapshot_dir is not None:
            if args.candidate_dir is None or args.apply or args.prepared_artifacts or args.database_url:
                raise ValueError("Snapshot mode requires --candidate-dir and cannot apply database changes")
            result = stage_local_snapshot(args.snapshot_dir, args.candidate_dir)
            print(json.dumps({"staged": str(args.candidate_dir), "bpmMetadataOverlay": result["bpmMetadataOverlay"]}, sort_keys=True))
            return 0
        if args.plan is None or args.candidate_dir is not None:
            raise ValueError("Database dry-run/apply requires --plan PATH")
        database_url = args.database_url or os.getenv("PUMBILITY_DATABASE_URL", "")
        require_loopback_database_url(database_url)
        if args.apply:
            if args.prepared_artifacts is not None:
                raise ValueError("Apply reads the saved plan only; do not replace its prepared artifacts")
            plan = json.loads(args.plan.read_text(encoding="utf-8"))
            with _connect(database_url) as connection:
                result = apply_migration(connection, plan)
        else:
            if args.plan.exists():
                raise ValueError("Refusing to overwrite an existing dry-run plan; choose a new path")
            prepared = json.loads(args.prepared_artifacts.read_text(encoding="utf-8")) if args.prepared_artifacts else None
            with _connect(database_url) as connection:
                plan = prepare_migration(connection, prepared_artifacts=prepared)
            args.plan.parent.mkdir(parents=True, exist_ok=True)
            args.plan.write_text(json.dumps(plan, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
            result = {"dryRun": True, "plan": str(args.plan), **plan["summary"], "planSha256": plan["planSha256"]}
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
