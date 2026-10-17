"""Build schema-54 SSS+ spread from pinned official boards and cached point fits."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from official_score_merge import merge_official_scores
from official_scoring_sources import load_official_scoring_manifest
from sss_scoring import MODES, payload_sha256
from sssplus_rare_continuous import build_rare_adjustments
from sssplus_spread import apply_sssplus_spread


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False), encoding="utf-8")
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, default=ROOT / ".local-data/piu-scores/combined/experiments/sssplus-median-v3/web_results.json")
    parser.add_argument("--manifest", type=Path, default=ROOT / ".local-data/piu-scores/official/scoring/manifest.json")
    parser.add_argument("--fits", type=Path, default=ROOT / ".local-data/sssplus-experiment")
    parser.add_argument("--output", type=Path, default=ROOT / ".local-data/piu-scores/combined/experiments/sssplus-spread/web_results.json")
    parser.add_argument("--private-output", type=Path, default=ROOT / ".local-data/sssplus-spread-experiment")
    parser.add_argument("--reuse-adjustments", action="store_true", help="Reuse only a private adjustment cache with matching pinned inputs.")
    parser.add_argument("--rare-adjustments", type=Path, help="Reuse an explicitly pinned adjustment artifact after its point fits were replayed separately.")
    args = parser.parse_args()
    base = json.loads(args.base.read_text(encoding="utf-8"))
    merged = merge_official_scores(load_official_scoring_manifest(args.manifest))
    if base["summary"]["method"]["sssplusScoring"].get("officialManifestSha256") != merged["manifestSha256"]:
        raise ValueError("Spread must reuse the baseline's pinned official manifest.")
    fits, boards, input_hashes = {}, {}, {"base": payload_sha256(base), "manifest": merged["manifestSha256"]}
    for mode, key in MODES:
        current = {str(r["chartId"]) for r in base[key] if r["level"] >= 20}
        boards[mode] = {cid: scores for cid, scores in merged["boards"].items() if cid in current}
        cached = json.loads((args.fits/f"official-{mode.lower()}-fit.json").read_text(encoding="utf-8"))
        if cached.get("identity", {}).get("boards") != payload_sha256(boards[mode]):
            raise ValueError("Cached point fit does not match the pinned mode's official boards.")
        fits[mode] = cached["fit"]
        if fits[mode].get("policy", {}).get("scoreTarget") != 995000:
            raise ValueError("Spread requires SSS+ cached fits.")
        input_hashes[mode] = payload_sha256(cached)
    identity = {"version": 1, "inputs": input_hashes}
    private_cache = args.private_output / "rare-adjustments.json"
    cached_adjustments = json.loads(private_cache.read_text(encoding="utf-8")) if args.reuse_adjustments and private_cache.exists() else None
    if args.rare_adjustments:
        replayed = json.loads(args.rare_adjustments.read_text(encoding="utf-8"))
        adjustments, policy = replayed["adjustments"], replayed["policy"]
    elif cached_adjustments and cached_adjustments.get("identity") == identity:
        adjustments, policy = cached_adjustments["adjustments"], cached_adjustments["policy"]
    else:
        adjustments, policy = build_rare_adjustments(base, boards, fits)
        _write(private_cache, {"identity": identity, "adjustments": adjustments, "policy": policy})
    result = apply_sssplus_spread(base, rare_adjustments=adjustments, rare_policy=policy)
    _write(args.output, result)
    spread = result["summary"]["method"]["sssplusSpreadPolicy"]
    report = {"schemaVersion": result["schemaVersion"], "output": str(args.output),
        "inputHashes": input_hashes, "rareCharts": len(adjustments), "dataFetches": 0,
        "fullPlayerRefits": 0, "pointReplay": "deterministic-cached-regularization-only",
        "tail": spread["tailPolicy"], "scale": spread["scale"]}
    _write(args.private_output / "verification.json", report)
    print(json.dumps(report))


if __name__ == "__main__":
    main()
