"""Stage public reference inputs for Vercel's Python service bundle."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_REFERENCE_PATHS = (
    Path("public/data/phoenix1.json"),
    Path("public/data/phoenix1.manifest.json"),
    Path("public/data/phoenix1-rerates.json"),
)
RUNTIME_REFERENCE_ROOT = PROJECT_ROOT / "runtime_reference_data"


def stage_reference_inputs(
    *, source_root: Path = PROJECT_ROOT, target_root: Path = RUNTIME_REFERENCE_ROOT
) -> dict[str, int]:
    target_root.mkdir(parents=True, exist_ok=True)
    total_bytes = 0
    for relative_path in PUBLIC_REFERENCE_PATHS:
        source = source_root / relative_path
        destination = target_root / relative_path.name
        shutil.copyfile(source, destination)
        total_bytes += destination.stat().st_size
    return {"files": len(PUBLIC_REFERENCE_PATHS), "bytes": total_bytes}


def main() -> int:
    sys.path.insert(0, str(PROJECT_ROOT))
    from scripts.build_song_bpm_runtime import build, render
    compiled = render(build(PROJECT_ROOT / "config"))
    if (PROJECT_ROOT / "config/phoenix2-song-bpm-runtime.json").read_text(encoding="utf-8") != compiled:
        raise RuntimeError("BPM runtime metadata is stale. Run npm run bpm:build before deployment.")
    counts = stage_reference_inputs()
    print(json.dumps({"status": "staged", **counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
