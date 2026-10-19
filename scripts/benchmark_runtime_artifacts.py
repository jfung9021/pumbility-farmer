"""Fixed synthetic benchmark of snapshot decode/hash versus pinned row receipts.

This isolates runtime I/O work. It does not fit target models or measure a
production refresh SLA. No environment credentials or production data are used.
"""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis_runtime import (_canonical_json_sha256, _load_typed_checkpoint_snapshot,
                              _verified_snapshot_reference, _write_typed_checkpoint_snapshot)
from mix_registry import resolve_mix
from piu_misgrade_analyzer import make_synthetic_snapshot


class SyntheticReceiptStore:
    def __init__(self):
        self.values = {}
        self.receipts = {}
        self.reset()

    def reset(self):
        self.reads = self.receipt_reads = self.decoded_bytes = 0

    def put_json(self, path, value):
        body = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.values[path] = body
        self.receipts[path] = {"artifactSha256": _canonical_json_sha256(value), "byteSize": len(body.encode()),
            "revision": "synthetic-1", "snapshotSha256": value["sha256"], "schemaVersion": str(value["schemaVersion"]),
            "jobId": value["jobId"], "mix": value["mix"]}

    def get_json(self, path):
        self.reads += 1
        self.decoded_bytes += len(self.values[path].encode())
        return json.loads(self.values[path])

    def typed_snapshot_receipt(self, path):
        self.receipt_reads += 1
        return copy.deepcopy(self.receipts.get(path))


def benchmark(*, iterations: int = 5, players_per_folder: int = 8):
    if not 1 <= iterations <= 100 or not 1 <= players_per_folder <= 80:
        raise ValueError("Synthetic benchmark limits are iterations 1–100 and players per folder 1–80.")
    players, charts, scores, _ = make_synthetic_snapshot(seed=20261021, players_per_folder=players_per_folder)
    snapshot = {"mix": "Phoenix2", "players": players, "charts": charts, "scores": scores}
    store, spec = SyntheticReceiptStore(), resolve_mix("phoenix2")
    reference = _write_typed_checkpoint_snapshot(store, job_id="synthetic-runtime-benchmark", mix_spec=spec, snapshot=snapshot)
    expected_rows = len(json.loads(store.values[reference["pathname"]])["snapshot"]["scores"])
    checkpoint = {"jobId": "synthetic-runtime-benchmark"}
    results = {}
    for name in ("fullValidation", "verifiedReference"):
        store.reset()
        started, cpu_started = time.perf_counter(), time.process_time()
        for _ in range(iterations):
            if name == "fullValidation":
                restored = _load_typed_checkpoint_snapshot(store, checkpoint=checkpoint, reference=reference, mix_spec=spec)
                if len(restored["scores"]) != expected_rows:
                    raise AssertionError("The synthetic snapshot differs from its verified reference.")
            elif not _verified_snapshot_reference(store, checkpoint=checkpoint, reference=reference, mix_spec=spec):
                raise AssertionError("The synthetic snapshot receipt was unavailable.")
        results[name] = {"durationMs": round((time.perf_counter() - started) * 1000, 3),
                         "cpuMs": round((time.process_time() - cpu_started) * 1000, 3),
                         "snapshotReads": store.reads, "receiptReads": store.receipt_reads,
                         "decodedBytes": store.decoded_bytes}
    return {"benchmark": "typed-snapshot-reference-io", "synthetic": True,
            "productionTiming": False, "iterations": iterations, "scoreRows": expected_rows,
            "verifiedPayloadParity": True, **results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--players-per-folder", type=int, default=8)
    arguments = parser.parse_args()
    print(json.dumps(benchmark(iterations=arguments.iterations, players_per_folder=arguments.players_per_folder), sort_keys=True))
