#!/usr/bin/env python3
"""Compare two privacy-safe migration manifests and require explained deltas."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.capture_pumbility_migration_baseline import (
    BaselineCaptureError,
    canonical_bytes,
    privacy_scan_manifest,
    validate_baseline_manifest,
)


COMPARISON_ROOTS = (
    "datasets",
    "publicArtifacts",
    "derivedArtifacts",
    "contractCoverage",
)
ALLOWED_CHANGE_REASONS = {
    "catalog-change",
    "consent-addition",
    "consent-revocation",
    "corrected-upstream-row",
    "documented-contract-change",
    "generation-publication",
    "methodology-change",
    "post-t0-score-addition",
    "retention-expiry",
    "schema-representation-change",
    "sync-boundary-advance",
}


class BaselineVerificationError(RuntimeError):
    """Raised when candidate parity cannot be demonstrated."""


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BaselineVerificationError(f"{label} file is missing: {path}") from None
    except json.JSONDecodeError:
        raise BaselineVerificationError(f"{label} file is not valid JSON: {path}") from None
    if not isinstance(value, dict):
        raise BaselineVerificationError(f"{label} must contain a JSON object.")
    return value


def _flatten(value: Any, prefix: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        if not value:
            return {prefix: {}}
        result: dict[str, Any] = {}
        for key in sorted(value):
            child_prefix = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(value[key], child_prefix))
        return result
    if isinstance(value, list):
        # Arrays are contracts whose order and membership are significant.
        return {prefix: value}
    return {prefix: value}


def _comparison_leaves(manifest: Mapping[str, Any]) -> dict[str, Any]:
    leaves: dict[str, Any] = {}
    for root in COMPARISON_ROOTS:
        if root not in manifest:
            raise BaselineVerificationError(
                f"Baseline manifest is missing comparison root {root!r}."
            )
        leaves.update(_flatten(manifest[root], root))
    return leaves


def _value_digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _validate_explanations(
    explanations: Mapping[str, Any],
    *,
    source_boundary: str,
    candidate_boundary: str,
) -> dict[str, dict[str, str]]:
    if explanations.get("schemaVersion") != 1:
        raise BaselineVerificationError("Explanation file schemaVersion must be 1.")
    if explanations.get("sourceBoundary") != source_boundary:
        raise BaselineVerificationError(
            "Explanation sourceBoundary does not match the source manifest."
        )
    if explanations.get("candidateBoundary") != candidate_boundary:
        raise BaselineVerificationError(
            "Explanation candidateBoundary does not match the candidate manifest."
        )
    changes = explanations.get("changes")
    if not isinstance(changes, list):
        raise BaselineVerificationError("Explanation file must contain a changes array.")
    result: dict[str, dict[str, str]] = {}
    for index, row in enumerate(changes):
        if not isinstance(row, Mapping):
            raise BaselineVerificationError(f"Explanation {index} is not an object.")
        path = str(row.get("path") or "").strip()
        reason = str(row.get("reason") or "").strip()
        evidence = str(row.get("evidence") or "").strip()
        if not path or not reason or not evidence:
            raise BaselineVerificationError(
                f"Explanation {index} requires path, reason, and evidence."
            )
        if reason not in ALLOWED_CHANGE_REASONS:
            raise BaselineVerificationError(
                f"Explanation {index} has unsupported reason {reason!r}."
            )
        if path in result:
            raise BaselineVerificationError(f"Explanation path {path!r} is duplicated.")
        result[path] = {"reason": reason, "evidence": evidence}
    return result


def compare_manifests(
    source: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    explanations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        validate_baseline_manifest(source)
        validate_baseline_manifest(candidate)
        privacy_scan_manifest(explanations or {})
    except BaselineCaptureError as exc:
        raise BaselineVerificationError(str(exc)) from exc

    source_boundary = str(source["boundary"]["id"])
    candidate_boundary = str(candidate["boundary"]["id"])
    explanation_map = _validate_explanations(
        explanations
        or {
            "schemaVersion": 1,
            "sourceBoundary": source_boundary,
            "candidateBoundary": candidate_boundary,
            "changes": [],
        },
        source_boundary=source_boundary,
        candidate_boundary=candidate_boundary,
    )
    source_values = _comparison_leaves(source)
    candidate_values = _comparison_leaves(candidate)
    all_paths = sorted(set(source_values) | set(candidate_values))
    exact_matches = 0
    explained_changes: list[dict[str, str]] = []
    unexplained: list[dict[str, str]] = []
    used_explanations: set[str] = set()
    missing = object()
    for path in all_paths:
        before = source_values.get(path, missing)
        after = candidate_values.get(path, missing)
        if before is not missing and after is not missing and canonical_bytes(before) == canonical_bytes(after):
            exact_matches += 1
            continue
        detail = {
            "path": path,
            "sourceValueSha256": _value_digest(None if before is missing else before),
            "candidateValueSha256": _value_digest(None if after is missing else after),
        }
        explanation = explanation_map.get(path)
        if explanation is None:
            unexplained.append(detail)
            continue
        used_explanations.add(path)
        explained_changes.append({"path": path, "reason": explanation["reason"]})

    unused = sorted(set(explanation_map) - used_explanations)
    report: dict[str, Any] = {
        "schemaVersion": 1,
        "sourceBoundary": source_boundary,
        "candidateBoundary": candidate_boundary,
        "exactMatches": exact_matches,
        "explainedChanges": len(explained_changes),
        "unexplainedMismatchCount": len(unexplained),
        "unusedExplanationCount": len(unused),
        "privacyScan": "passed",
        "result": "passed" if not unexplained and not unused else "failed",
        "explainedChangePaths": explained_changes,
        "unexplainedMismatchPaths": unexplained,
        "unusedExplanationPaths": unused,
    }
    privacy_scan_manifest(report)
    return report


def verify_manifests(
    source: Mapping[str, Any],
    candidate: Mapping[str, Any],
    *,
    explanations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report = compare_manifests(source, candidate, explanations=explanations)
    if report["result"] != "passed":
        raise BaselineVerificationError(
            "Migration baseline verification failed: "
            f"{report['unexplainedMismatchCount']} unexplained mismatches and "
            f"{report['unusedExplanationCount']} unused explanations."
        )
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify candidate data against a privacy-safe migration baseline."
    )
    parser.add_argument("source", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--explanations", type=Path)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        source = _read_object(args.source, "Source manifest")
        candidate = _read_object(args.candidate, "Candidate manifest")
        explanations = (
            _read_object(args.explanations, "Explanation")
            if args.explanations
            else None
        )
        report = compare_manifests(source, candidate, explanations=explanations)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(
                json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        print(json.dumps(report, separators=(",", ":"), sort_keys=True))
        return 0 if report["result"] == "passed" else 1
    except BaselineVerificationError as exc:
        print(f"Baseline verification failed safely: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
