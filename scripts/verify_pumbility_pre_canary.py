"""Run the read-only evidence gate immediately before a Supabase read canary.

Run this command through ``vercel env run -e production`` while the production
deployment is still in the documented fail-open shadow state.  It never edits
environment variables, artifacts, database rows, or deployment configuration.
It first proves that the process received the safe production flags, executes
the focused local regression checks, and then performs the existing exact
production-to-Supabase reconciliation against a stable Vercel boundary.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pumbility_store import (  # noqa: E402
    BLOB_MIRROR_ENV,
    BLOB_READ_FALLBACK_ENV,
    CANONICAL_SNAPSHOT_WRITE_ENV,
    SHADOW_STRICT_ENV,
    _enabled,
    configured_backend,
    configured_read_canaries,
    validate_rollout_configuration,
)
from scripts.reconcile_pumbility_production import (  # noqa: E402
    main as reconcile_production,
)


PLAYER_REFRESH_ENV = "PLAYER_RECOMMENDATION_REFRESH_ENABLED"
REQUIRED_RECONCILIATION_STAGES = (
    "source-boundary",
    "relational",
    "model-json",
    "pointers",
    "numeric-model",
    "player-caches",
    "artifacts",
    "boundary",
)
REGRESSION_TESTS = (
    "tests.test_pumbility_store.PumbilityArtifactStoreTests."
    "test_json_digest_uses_database_normalized_numeric_value",
    "tests.test_verify_pumbility_pre_canary.ArtifactIntegritySafetyTests."
    "test_json_read_rejects_digest_or_byte_size_mismatch",
    "tests.test_verify_pumbility_pre_canary.ArtifactIntegritySafetyTests."
    "test_json_read_rejects_wrong_schema_migration",
    "tests.test_pumbility_store.ReadCanaryTests."
    "test_json_candidate_is_served_only_after_exact_equality",
    "tests.test_pumbility_store.ReadCanaryTests."
    "test_json_candidate_failure_falls_back_without_changing_writes",
    "tests.test_pumbility_store.SupabasePrimaryStoreTests."
    "test_missing_or_failed_primary_read_uses_enabled_legacy_fallback",
    "tests.test_pumbility_migration_baseline.CanonicalHashTests."
    "test_privacy_scan_rejects_private_fields_credentials_and_identifiers",
    "tests.test_reconcile_pumbility_production.ProductionReconciliationTargetTests."
    "test_reconciliation_rejects_changed_json_payload",
    "tests.test_reconcile_pumbility_production.ProductionReconciliationTargetTests."
    "test_reconciliation_rejects_target_only_cached_player_objects",
)


class PreCanaryGateError(RuntimeError):
    """A pre-canary requirement was not proven."""

    def __init__(
        self,
        message: str,
        *,
        safe_evidence: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.safe_evidence = dict(safe_evidence or {})


def reconciliation_failure_evidence(output: str) -> dict[str, object]:
    """Retain only allowlisted aggregate fields from a failed reconciliation."""
    events: list[Mapping[str, Any]] = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, Mapping):
            events.append(event)
    completed = [
        str(event.get("stage"))
        for event in events
        if event.get("status") == "stage-completed"
        and event.get("stage") in REQUIRED_RECONCILIATION_STAGES
    ]
    failure_event: dict[str, object] = {}
    for event in reversed(events):
        if event.get("status") not in {
            "integrity-failed",
            "read-failed",
            "mismatch",
        }:
            continue
        for key in (
            "status",
            "stage",
            "artifactIndex",
            "side",
            "errorType",
            "digestMatches",
            "byteSizeMatches",
            "unexplainedMismatchCount",
        ):
            value = event.get(key)
            if isinstance(value, (str, int, bool)):
                failure_event[key] = value
        break
    return {
        "completedStages": completed,
        "failureEvent": failure_event,
    }


def assert_pre_canary_environment(environment: Mapping[str, str]) -> dict[str, object]:
    """Require the exact non-authoritative flag state without printing values."""
    validate_rollout_configuration(environment)
    backend = configured_backend(environment)
    if backend not in {"vercel", "shadow"}:  # pragma: no cover - validated above
        raise PreCanaryGateError("The pre-canary gate requires Vercel authority.")
    if _enabled(environment.get(SHADOW_STRICT_ENV)):
        raise PreCanaryGateError("Strict shadow mode must remain disabled before canary.")
    canonical_shadow_writes = _enabled(
        environment.get(CANONICAL_SNAPSHOT_WRITE_ENV)
    )
    if backend == "shadow" and not canonical_shadow_writes:
        raise PreCanaryGateError(
            "Canonical shadow writes must match the already-accepted shadow state."
        )
    if _enabled(environment.get(BLOB_MIRROR_ENV)) or _enabled(
        environment.get(BLOB_READ_FALLBACK_ENV)
    ):
        raise PreCanaryGateError(
            "Blob mirror and primary-read fallback are cutover-only controls."
        )
    if configured_read_canaries(environment):
        raise PreCanaryGateError("The read-canary allowlist must still be empty.")
    if _enabled(environment.get(PLAYER_REFRESH_ENV)):
        raise PreCanaryGateError(
            "Selected-player recommendation refresh must remain frozen."
        )
    return {
        "productionBackend": backend,
        "vercelAuthoritative": True,
        "strictShadow": False,
        "canonicalShadowWrites": canonical_shadow_writes,
        "blobMirror": False,
        "blobReadFallback": False,
        "readCanary": False,
        "selectedPlayerRefresh": False,
    }


def verify_reconciliation_output(output: str) -> dict[str, object]:
    """Validate only the reconciler's aggregate-safe JSON evidence contract."""
    events: list[Mapping[str, Any]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise PreCanaryGateError(
                "Production reconciliation emitted non-JSON evidence."
            ) from error
        if not isinstance(event, Mapping):
            raise PreCanaryGateError(
                "Production reconciliation emitted malformed evidence."
            )
        events.append(event)

    completed = tuple(
        str(event.get("stage"))
        for event in events
        if event.get("status") == "stage-completed"
    )
    if completed != REQUIRED_RECONCILIATION_STAGES:
        raise PreCanaryGateError(
            "Production reconciliation did not complete every required stage in order."
        )
    passed = [event for event in events if event.get("status") == "passed"]
    if len(passed) != 1:
        raise PreCanaryGateError(
            "Production reconciliation has no unique passing summary."
        )
    summary = passed[0]
    production_backend = summary.get("productionBackend")
    if production_backend not in {"vercel", "shadow"}:
        raise PreCanaryGateError("Reconciliation did not retain Vercel authority.")
    if summary.get("privacyScan") != "passed":
        raise PreCanaryGateError("Reconciliation privacy evidence did not pass.")
    if summary.get("unexplainedMismatchCount") != 0:
        raise PreCanaryGateError("Reconciliation has unexplained differences.")

    mixes = summary.get("mixes")
    if not isinstance(mixes, Mapping) or set(mixes) != {"phoenix1", "phoenix2"}:
        raise PreCanaryGateError("Reconciliation did not cover both production mixes.")
    exact_matches: dict[str, int] = {}
    for mix in ("phoenix1", "phoenix2"):
        result = mixes[mix]
        if not isinstance(result, Mapping):
            raise PreCanaryGateError("Reconciliation mix evidence is malformed.")
        matches = result.get("exactMatches")
        if (
            isinstance(matches, bool)
            or not isinstance(matches, int)
            or matches <= 0
            or result.get("unexplainedMismatchCount") != 0
        ):
            raise PreCanaryGateError("A production mix lacks exact parity evidence.")
        exact_matches[mix] = matches

    artifacts = summary.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise PreCanaryGateError("Artifact reconciliation evidence is missing.")
    artifact_counts: dict[str, int] = {}
    for name in ("jsonArtifacts", "binaryArtifacts", "cachedPlayerArtifacts"):
        count = artifacts.get(name)
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise PreCanaryGateError("Artifact reconciliation evidence is incomplete.")
        artifact_counts[name] = count

    return {
        "exactRelationalParity": True,
        "exactArtifactParity": True,
        "stableBoundary": True,
        "privacyScan": "passed",
        "productionBackend": production_backend,
        "exactMatches": exact_matches,
        "artifacts": artifact_counts,
    }


def run_regression_checks(
    *,
    command_runner: Any = subprocess.run,
) -> int:
    """Execute the existing focused safety contracts without production I/O."""
    result = command_runner(
        [sys.executable, "-m", "unittest", "-v", *REGRESSION_TESTS],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise PreCanaryGateError("A focused pre-canary regression check failed.")
    return len(REGRESSION_TESTS)


def run_exact_reconciliation() -> dict[str, object]:
    """Run the existing read-only reconciler and retain only sanitized evidence."""
    captured = io.StringIO()
    try:
        with redirect_stdout(captured):
            result = reconcile_production(allow_canonical_shadow_writes=True)
    except Exception:
        raise PreCanaryGateError(
            "Production reconciliation did not pass.",
            safe_evidence=reconciliation_failure_evidence(captured.getvalue()),
        ) from None
    if result != 0:
        raise PreCanaryGateError("Production reconciliation did not pass.")
    return verify_reconciliation_output(captured.getvalue())


def main() -> int:
    flags = assert_pre_canary_environment(os.environ)
    regression_count = run_regression_checks()
    reconciliation = run_exact_reconciliation()
    if reconciliation["productionBackend"] != flags["productionBackend"]:
        raise PreCanaryGateError(
            "Reconciliation did not observe the validated production backend."
        )
    print(
        json.dumps(
            {
                "status": "passed",
                "gate": "pre-canary",
                "safeFlags": flags,
                "focusedRegressionChecks": regression_count,
                "schemaMigrationCheck": "passed",
                **reconciliation,
                "privateValuesPrinted": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        print(
            "Pumbility pre-canary evidence gate failed safely; private details were suppressed.",
            file=sys.stderr,
        )
        raise SystemExit(2) from None
