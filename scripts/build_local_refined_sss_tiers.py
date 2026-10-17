#!/usr/bin/env python3
"""Build the local second-pass SSS / official D26+ Clearing candidate from pinned inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cross_level_clearing import current_clearing_catalog
from official_score_merge import merge_official_scores
from official_scoring_sources import load_official_scoring_manifest
from refined_tiers import apply_refined_tiers
from scripts.build_local_recommendations import _read_snapshot
from scripts.build_local_sss_tiers import _core_code_identity, _valid_replay_policy, _write
from sss_scoring import MODES, payload_sha256, prepare_target_observations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=ROOT / ".local-data/sss-refined-experiment/schema49-baseline.json")
    parser.add_argument("--manifest", type=Path, default=ROOT / ".local-data/piu-scores/official/scoring/manifest.json")
    parser.add_argument("--fit-directory", type=Path, default=ROOT / ".local-data/sss-scoring-experiment")
    parser.add_argument("--output", type=Path, default=ROOT / ".local-data/piu-scores/combined/experiments/sss-refined/web_results.json")
    parser.add_argument("--private-output", type=Path, default=ROOT / ".local-data/sss-refined-experiment")
    args = parser.parse_args()
    started = time.monotonic()
    base = json.loads(args.base.read_text(encoding="utf-8"))
    first, second = _read_snapshot("phoenix1"), _read_snapshot("phoenix2")
    catalog = current_clearing_catalog(base, second)
    manifest = load_official_scoring_manifest(args.manifest)
    merged = merge_official_scores(manifest)
    prepared = prepare_target_observations(first, second, merged, catalog)
    source_hashes = {"phoenix1": payload_sha256(first), "phoenix2": payload_sha256(second),
                     "currentCatalog": payload_sha256(catalog), "officialManifest": merged["manifestSha256"]}
    if source_hashes != base["summary"]["method"]["sssScoring"]["sourceHashes"]:
        raise ValueError("Pinned inputs differ from the frozen first-pass SSS inputs.")
    fits = {"submitted": {}, "official": {}}
    for source, minimum in (("official", 20), ("submitted", 16)):
        for mode, _ in MODES:
            boards = {cid: scores for cid, scores in prepared[source]["boards"].items()
                      if prepared["catalog"][cid]["type"] == mode and prepared["catalog"][cid]["level"] >= minimum}
            source_boards = {name: {cid: entries for cid, entries in mix.items() if cid in boards}
                             for name, mix in prepared[source]["sourceBoards"].items()}
            cache = json.loads((args.fit_directory / f"{source}-{mode.lower()}-fit.json").read_text(encoding="utf-8"))
            identity = cache["identity"]
            if (identity["boards"] != payload_sha256(boards) or identity["sourceBoards"] != payload_sha256(source_boards)
                    or not _valid_replay_policy(cache["fit"], 200)
                    or cache["codeIdentity"]["coreCodeSha256"] != _core_code_identity()):
                raise ValueError(f"Cached {source}/{mode} first-pass fit is not a valid replay.")
            fits[source][mode] = cache["fit"]
    print(json.dumps({"stage": "pinned-inputs-validated", "baseSchema": base["schemaVersion"], "sourceHashes": source_hashes}), flush=True)
    result, audit = apply_refined_tiers(base, prepared, fits, manifest["_captures"]["Phoenix2"])
    _write(args.output, result)
    audit.update(schemaVersion=result["schemaVersion"], output=str(args.output),
                 baselineFileSha256=hashlib.sha256(args.base.read_bytes()).hexdigest(),
                 basePayloadSha256=payload_sha256(base), sourceHashes=source_hashes,
                 elapsedSeconds=round(time.monotonic() - started, 2))
    _write(args.private_output / "verification.json", audit)
    print(json.dumps({"stage": "complete", "schemaVersion": result["schemaVersion"], "output": str(args.output),
                      "elapsedSeconds": audit["elapsedSeconds"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
