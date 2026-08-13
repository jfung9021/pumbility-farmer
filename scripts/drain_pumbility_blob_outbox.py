"""Replay durable Supabase-to-Vercel rollback-mirror intents.

The outbox stores object references and operation metadata only. Artifact JSON
and binary contents are fetched from the Supabase-primary store at delivery
time and are never printed by this command.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from analysis_runtime import VercelPrivateBlobStore  # noqa: E402
from pumbility_store import (  # noqa: E402
    BACKEND_ENV,
    BLOB_MIRROR_ENV,
    PumbilityArtifactStore,
    _enabled,
    configured_backend,
    drain_blob_mirror_outbox,
)


CONFIRMATION_ENV = "PUMBILITY_BLOB_OUTBOX_CONFIRMATION"
CONFIRMATION = "DRAIN PUMBILITY BLOB OUTBOX"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Replay pending mirror events after the explicit confirmation gate.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.apply:
        raise RuntimeError("Outbox replay is read/write and requires --apply.")
    if configured_backend() != "supabase":
        raise RuntimeError(f"{BACKEND_ENV}=supabase is required for outbox replay.")
    if not _enabled(os.getenv(BLOB_MIRROR_ENV)):
        raise RuntimeError(f"{BLOB_MIRROR_ENV}=true is required for outbox replay.")
    if os.getenv(CONFIRMATION_ENV, "").strip() != CONFIRMATION:
        raise RuntimeError(f"Set {CONFIRMATION_ENV} to the documented confirmation phrase.")

    completed, failed = drain_blob_mirror_outbox(
        PumbilityArtifactStore(), VercelPrivateBlobStore(), limit=args.limit
    )
    print(
        {
            "completed": completed,
            "failed": failed,
            "artifactReferencesPrinted": False,
        }
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
