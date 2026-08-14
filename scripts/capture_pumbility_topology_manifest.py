"""Create a sanitized, fail-closed manifest for a topology qualification.

The two input files are operator-exported deployment metadata.  This command
does not contact Vercel or any application endpoint.  It emits labels and safe
configuration facts only; deployment identifiers, origins, environment values,
and private hashes are deliberately excluded.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_DATA = PROJECT_ROOT / ".local-data"
LABEL_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")
ALLOWED_KEYS = frozenset(
    {
        "schemaVersion",
        "label",
        "region",
        "gitCommit",
        "sourceSha256",
        "lockSha256",
        "runtime",
        "memoryMb",
        "maxDurationSeconds",
        "workerConcurrency",
        "databaseConnectionLimit",
        "connectionStrategy",
        "environmentKeyNames",
        "rolloutFlags",
    }
)
REQUIRED_ENVIRONMENT_KEYS = frozenset(
    {
        "PUMBILITY_DATABASE_URL",
        "BLOB_READ_WRITE_TOKEN",
        "QSTASH_TOKEN",
    }
)
SAFE_FLAG_KEYS = frozenset(
    {
        "backend",
        "shadowStrict",
        "canonicalSnapshotWriteEnabled",
        "blobMirrorEnabled",
        "blobReadFallbackEnabled",
        "readCanaryDomains",
        "selectedPlayerRefreshEnabled",
    }
)


class ManifestError(RuntimeError):
    """Deployment evidence is incomplete, unsafe, or not comparable."""


def _mapping(path: Path, *, allowed_keys: frozenset[str] | None = ALLOWED_KEYS) -> Mapping[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ManifestError("Deployment metadata must be a JSON object.")
    unknown = set(value) - allowed_keys if allowed_keys is not None else set()
    if unknown:
        raise ManifestError("Deployment metadata contains non-allowlisted fields.")
    return value


def _label(value: object) -> str:
    normalized = str(value or "").strip().casefold()
    if not LABEL_RE.fullmatch(normalized):
        raise ManifestError("A deployment label is malformed.")
    return normalized


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ManifestError(f"{field} must be a positive integer.")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(
        isinstance(item, str) and item for item in value
    ):
        raise ManifestError(f"{field} must be a non-empty string list.")
    if len(set(value)) != len(value):
        raise ManifestError(f"{field} contains duplicates.")
    return tuple(sorted(value))


def _safe_flags(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping) or set(value) != SAFE_FLAG_KEYS:
        raise ManifestError("The complete sanitized rollout flag set is required.")
    backend = value.get("backend")
    if backend not in {"vercel", "shadow"}:
        raise ManifestError("The topology is not Vercel-authoritative.")
    expected_bool_fields = (
        "shadowStrict",
        "canonicalSnapshotWriteEnabled",
        "blobMirrorEnabled",
        "blobReadFallbackEnabled",
        "selectedPlayerRefreshEnabled",
    )
    if any(type(value.get(field)) is not bool for field in expected_bool_fields):
        raise ManifestError("Sanitized rollout flags have invalid types.")
    domains = value.get("readCanaryDomains")
    if not isinstance(domains, list) or domains:
        raise ManifestError("Read-canary domains must remain empty during qualification.")
    if value["shadowStrict"]:
        raise ManifestError("Strict shadow mode is not an accepted safe topology state.")
    if value["blobMirrorEnabled"] or value["blobReadFallbackEnabled"]:
        raise ManifestError("Cutover-only Blob controls must remain disabled.")
    if value["selectedPlayerRefreshEnabled"]:
        raise ManifestError("Selected-player refresh must remain frozen.")
    if backend == "vercel" and value["canonicalSnapshotWriteEnabled"]:
        raise ManifestError("Canonical writes cannot be enabled in Vercel-only mode.")
    return {key: value[key] for key in sorted(value)}


def _validated_metadata(value: Mapping[str, Any]) -> dict[str, object]:
    if set(value) - ALLOWED_KEYS:
        raise ManifestError("Deployment metadata contains non-allowlisted fields.")
    if value.get("schemaVersion") != 1:
        raise ManifestError("Unsupported deployment metadata schema.")
    git_commit = str(value.get("gitCommit") or "").casefold()
    source_hash = str(value.get("sourceSha256") or "").casefold()
    lock_hash = str(value.get("lockSha256") or "").casefold()
    if not COMMIT_RE.fullmatch(git_commit):
        raise ManifestError("gitCommit is malformed.")
    if not SHA256_RE.fullmatch(source_hash) or not SHA256_RE.fullmatch(lock_hash):
        raise ManifestError("Source and lock identities must be SHA-256 values.")
    runtime = str(value.get("runtime") or "")
    region = _label(value.get("region"))
    strategy = str(value.get("connectionStrategy") or "")
    if runtime not in {"python3.12", "python3.13"}:
        raise ManifestError("The runtime is not allowlisted.")
    if strategy not in {"transaction-pooler", "session-pooler", "direct"}:
        raise ManifestError("The connection strategy is not allowlisted.")
    environment_keys = _strings(value.get("environmentKeyNames"), "environmentKeyNames")
    if not REQUIRED_ENVIRONMENT_KEYS.issubset(environment_keys):
        raise ManifestError("Required environment key names are missing.")
    return {
        "label": _label(value.get("label")),
        "region": region,
        "gitCommit": git_commit,
        "sourceSha256": source_hash,
        "lockSha256": lock_hash,
        "runtime": runtime,
        "memoryMb": _positive_int(value.get("memoryMb"), "memoryMb"),
        "maxDurationSeconds": _positive_int(
            value.get("maxDurationSeconds"), "maxDurationSeconds"
        ),
        "workerConcurrency": _positive_int(
            value.get("workerConcurrency"), "workerConcurrency"
        ),
        "databaseConnectionLimit": _positive_int(
            value.get("databaseConnectionLimit"), "databaseConnectionLimit"
        ),
        "connectionStrategy": strategy,
        "environmentKeyNames": environment_keys,
        "rolloutFlags": _safe_flags(value.get("rolloutFlags")),
    }


def create_manifest(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    *,
    topology_kind: str,
    boundary: Mapping[str, Any],
) -> dict[str, object]:
    first_value = _validated_metadata(first)
    second_value = _validated_metadata(second)
    if first_value["label"] == second_value["label"]:
        raise ManifestError("Deployment labels must be different.")
    if boundary.get("schemaVersion") != 1:
        raise ManifestError("Unsupported stable-boundary evidence schema.")
    boundary_keys = {
        "schemaVersion",
        "publicationFrozen",
        "exactReconciliationPassed",
        "firstBoundarySha256",
        "secondBoundarySha256",
    }
    if set(boundary) != boundary_keys:
        raise ManifestError("Stable-boundary evidence contains unexpected fields.")
    first_boundary = str(boundary.get("firstBoundarySha256") or "").casefold()
    second_boundary = str(boundary.get("secondBoundarySha256") or "").casefold()
    boundary_passed = bool(
        boundary.get("publicationFrozen") is True
        and boundary.get("exactReconciliationPassed") is True
        and SHA256_RE.fullmatch(first_boundary)
        and first_boundary == second_boundary
    )
    if not boundary_passed:
        raise ManifestError("The exact stable data boundary is not proven.")

    controlled_field = "region" if topology_kind == "region" else "connectionStrategy"
    comparable_fields = (
        "gitCommit",
        "sourceSha256",
        "lockSha256",
        "runtime",
        "memoryMb",
        "maxDurationSeconds",
        "workerConcurrency",
        "databaseConnectionLimit",
        "environmentKeyNames",
        "rolloutFlags",
    )
    if any(first_value[field] != second_value[field] for field in comparable_fields):
        raise ManifestError("The deployments differ outside the controlled variable.")
    other_field = "connectionStrategy" if controlled_field == "region" else "region"
    if first_value[other_field] != second_value[other_field]:
        raise ManifestError("The deployments differ outside the controlled variable.")
    if first_value[controlled_field] == second_value[controlled_field]:
        raise ManifestError("The controlled topology variable does not differ.")

    def sanitized(value: Mapping[str, object]) -> dict[str, object]:
        return {
            "label": value["label"],
            "region": value["region"],
            "runtime": value["runtime"],
            "memoryMb": value["memoryMb"],
            "maxDurationSeconds": value["maxDurationSeconds"],
            "workerConcurrency": value["workerConcurrency"],
            "databaseConnectionLimit": value["databaseConnectionLimit"],
            "connectionStrategy": value["connectionStrategy"],
            "environmentKeyNames": value["environmentKeyNames"],
            "rolloutFlags": value["rolloutFlags"],
        }

    return {
        "schemaVersion": 1,
        "status": "passed",
        "topologyKind": topology_kind,
        "controlledDifference": controlled_field,
        "deployments": [sanitized(first_value), sanitized(second_value)],
        "identities": {
            "gitCommitMatch": True,
            "sourceHashMatch": True,
            "lockHashMatch": True,
            "privateHashesDisclosed": False,
        },
        "stableDataBoundary": {
            "publicationFrozen": True,
            "exactReconciliationPassed": True,
            "exactFingerprintMatch": True,
            "privateFingerprintDisclosed": False,
        },
        "safeFlagsProven": True,
    }


def _local_output(path: Path) -> Path:
    resolved = path.resolve()
    local_root = LOCAL_DATA.resolve()
    if resolved == local_root or not resolved.is_relative_to(local_root):
        raise ManifestError("The output must be a file below .local-data.")
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--stable-boundary", type=Path, required=True)
    parser.add_argument("--topology-kind", choices=("region", "connection"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = create_manifest(
        _mapping(args.first),
        _mapping(args.second),
        topology_kind=args.topology_kind,
        boundary=_mapping(args.stable_boundary, allowed_keys=None),
    )
    output = _local_output(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    output.write_text(encoded, encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Pumbility topology manifest capture failed safely; private details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
