"""Synthetic-only numerical benchmark. Run each worker count in a fresh process.

python -m benchmarks.benchmark_target_reuse --case reuse
python -m benchmarks.benchmark_target_reuse --case backfill --workers 1
python -m benchmarks.benchmark_target_reuse --case backfill --workers 2
No network, production state, raw observations or private identifiers are emitted.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import sys
import time
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from analysis_runtime import MemoryBlobStore
import production_tiers as tiers
import sss_scoring_model as model
from target_fit_cache import load_or_fit_target


def memory():
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("faults", wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in ("peak", "working", "quotaPeakPaged", "quotaPaged",
                    "quotaPeakNonPaged", "quotaNonPaged", "pagefile", "peakPagefile")]
        value = Counters()
        value.cb = ctypes.sizeof(value)
        process = ctypes.windll.kernel32.GetCurrentProcess
        process.restype = wintypes.HANDLE
        read = ctypes.windll.psapi.GetProcessMemoryInfo
        read.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
        if read(process(), ctypes.byref(value), value.cb):
            return {"rss_bytes": value.working, "peak_rss_bytes": value.peak}
    else:
        import resource
        from pathlib import Path
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        resident = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        return {"rss_bytes": resident, "peak_rss_bytes": peak * (1 if sys.platform == "darwin" else 1024)}
    return {}


def fixture():
    rng = np.random.default_rng(31)
    return {str(chart): {str(player): 995000 if rng.random() <
        1 / (1 + np.exp(-(player / 8 - 2 - chart / 5))) else 975000
        for player in range(32)} for chart in range(8)}


def reuse_cases():
    boards, store = fixture(), MemoryBlobStore()
    same_band = {cid: {p: s+1 for p, s in board.items()} for cid, board in boards.items()}
    crossing = copy.deepcopy(same_band)
    crossing["0"]["0"] = 975000 if crossing["0"]["0"] >= 980000 else 995000
    rows = []
    for label, values in (("cold", boards), ("same-input", boards),
                          ("same-band-score-change", same_band), ("target-crossing", crossing)):
        predictor = tiers.training_predictor(980000, store)
        expected = model.fit_target_model(values, score_target=980000, bootstrap_samples=200,
                                         seed=tiers.SEED, general_predictor=tiers.training_predictor(980000))
        started, cpu = time.perf_counter(), time.process_time()
        with patch.object(model, "_fit", wraps=model._fit) as fits:
            actual = load_or_fit_target(store, values, score_target=980000, bootstrap_samples=200,
                                       seed=tiers.SEED, general_predictor=predictor)
        rows.append({"case": label, "seconds": time.perf_counter()-started,
            "cpu_seconds": time.process_time()-cpu, "optimizer_calls": fits.call_count,
            "bootstrap_completed": actual["diagnostics"]["bootstrap"]["completed"],
            "bootstrap_failed": actual["diagnostics"]["bootstrap"]["failed"],
            "exact_reference_parity": actual == expected, **memory()})
    return rows


def backfill_case(workers):
    # Inject one fixed normalized epoch so this measures real bounded cohort
    # execution/calibration, independently of synchronization/network latency.
    base = fixture()
    catalog = {f"{mode}-{cid}": {"id": f"{mode}-{cid}", "type": mode,
        "level": 20+int(cid)//4, "noteCount": 100, "songName": f"Synthetic {mode} {cid}"}
        for mode in ("Single", "Double") for cid in base}
    prepared = {"catalog": catalog}
    for source in ("official", "submitted"):
        boards = {cid: {f"{source}:{player}": score for player, score in base[cid.split("-")[1]].items()}
                  for cid in catalog}
        prepared[source] = {"boards": boards, "sourceBoards": {"fixture": boards},
            "charts": {cid: {"anchorEligible": True, "comparability": "comparable", "possiblyTruncated": False}
                       for cid in catalog}}
    prepared.update(manifestSha256="synthetic", sourceDates={})
    snapshot = {"charts": list(catalog.values()), "scores": []}
    context = (snapshot, snapshot, list(catalog.values()),
               SimpleNamespace(public=lambda: {"fixture": True}, semantic_hash=lambda: "synthetic-policy"),
               {"manifestSha256": "synthetic"}, prepared)
    store = MemoryBlobStore()
    inputs = tiers.pin_tier_publication_inputs(store, first=snapshot, second=snapshot, manifest={},
        records=[], metadata={}, generated_at_utc="2026-10-20T00:00:00Z")
    started, cpu = time.perf_counter(), time.process_time()
    invocations = 0
    with patch("production_tiers.load_context", return_value=context), \
         patch("official_scoring_sources.canonical_scoring_manifest_sha256", return_value="synthetic"):
        while True:
            result = tiers.advance_goal_backfill(store, pinned_inputs_reference=inputs,
                                                 goal="ss", max_fit_workers=workers)
            invocations += 1
            if result["done"]:
                break
    return {"case": "backfill", "workers": workers, "invocations": invocations,
        "seconds": time.perf_counter()-started, "cpu_seconds": time.process_time()-cpu,
        "artifact_sha256": result["artifactReference"]["sha256"],
        "completed_cohorts": result["completedTargetFits"], **memory()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=("reuse", "backfill"), required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2), default=1)
    args = parser.parse_args()
    print(json.dumps(reuse_cases() if args.case == "reuse" else backfill_case(args.workers), sort_keys=True))
