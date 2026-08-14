"""Default-off protected Preview operator diagnostics."""

from __future__ import annotations

import os
from typing import Mapping

from fastapi import APIRouter
from fastapi.responses import JSONResponse


router = APIRouter()
PRECANARY_DIAGNOSTIC_ENV = "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED"
PRECANARY_ARTIFACT_REPAIR_ENV = "PUMBILITY_PRECANARY_ARTIFACT_REPAIR_ENABLED"


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _run_hosted_gate(environment: Mapping[str, str]) -> dict[str, object]:
    # Keep the reconciliation-only operator path out of ordinary API startup.
    from scripts.verify_pumbility_pre_canary import (
        PreCanaryGateError,
        assert_pre_canary_environment,
        run_exact_reconciliation,
    )

    flags = assert_pre_canary_environment(environment)
    reconciliation = run_exact_reconciliation()
    if reconciliation["productionBackend"] != flags["productionBackend"]:
        raise PreCanaryGateError(
            "Hosted reconciliation did not observe the validated backend."
        )
    return {
        "status": "passed",
        "gate": "hosted-pre-canary-reconciliation",
        "safeFlags": flags,
        "reconciliation": reconciliation,
    }


def _safe_failure_evidence(
    environment: Mapping[str, str], error: Exception
) -> dict[str, object]:
    """Classify a hosted failure without returning exception text or private values."""
    message = str(error)
    if not environment.get("PUMBILITY_DATABASE_URL", "").strip() or len(
        environment.get("BLOB_READ_WRITE_TOKEN", "").encode("utf-8")
    ) < 32:
        failure_code = "credentials-unavailable"
    elif type(error).__name__ == "PreCanaryGateError":
        failure_code = "pre-canary-contract"
    elif type(error).__module__.startswith("psycopg"):
        failure_code = "database-operation"
    elif "boundary" in message.casefold() or "source" in message.casefold():
        failure_code = "source-boundary"
    elif "relational" in message.casefold() or "database" in message.casefold():
        failure_code = "relational-reconciliation"
    elif any(
        token in message.casefold()
        for token in ("artifact", "pointer", "numeric model", "cached player")
    ):
        failure_code = "artifact-reconciliation"
    else:
        failure_code = "reconciliation-runtime"
    evidence = {
        "failureCode": failure_code,
        "databaseConfigured": bool(
            environment.get("PUMBILITY_DATABASE_URL", "").strip()
        ),
        "blobConfigured": len(
            environment.get("BLOB_READ_WRITE_TOKEN", "").encode("utf-8")
        ) >= 32,
    }
    safe_reconciliation = getattr(error, "safe_evidence", None)
    if isinstance(safe_reconciliation, Mapping):
        evidence["reconciliation"] = dict(safe_reconciliation)
    return evidence


def _repair_numeric_artifact(environment: Mapping[str, str]) -> dict[str, object]:
    """Repair only the active numeric model under the production backfill guards."""
    import psycopg

    from analysis_runtime import VercelPrivateBlobStore
    from pumbility_store import PumbilityArtifactStore, _assert_schema
    from scripts.backfill_pumbility_production import (
        _assert_boundary_unchanged,
        _assert_database_target,
        _claim_lock,
        _read_stable_boundary,
        _recommendation_paths,
        _release_lock,
    )
    from scripts.capture_pumbility_migration_baseline import (
        _required_production_bytes,
    )
    from scripts.reconcile_pumbility_production import session_url_from_runtime
    from scripts.verify_pumbility_pre_canary import assert_pre_canary_environment

    assert_pre_canary_environment(environment)
    session_url = session_url_from_runtime(
        environment.get("PUMBILITY_DATABASE_URL", "").strip()
    )
    source = VercelPrivateBlobStore()
    pointers, _phoenix1, phoenix2 = _read_stable_boundary(source)
    _json_paths, numeric_path = _recommendation_paths(pointers["recommendations"])
    numeric = _required_production_bytes(
        source, numeric_path, "recommendation numeric model"
    )
    target = PumbilityArtifactStore(
        database_url=session_url,
        supabase_url=environment.get("PUMBILITY_SUPABASE_URL", ""),
        service_key=environment.get("PUMBILITY_SUPABASE_SERVICE_ROLE_KEY", ""),
        bucket=environment.get("PUMBILITY_STORAGE_BUCKET", ""),
    )

    with psycopg.connect(session_url, prepare_threshold=None) as connection:
        with connection.cursor() as cursor:
            _assert_schema(cursor)
            _assert_database_target(cursor)
            _claim_lock(cursor)
        connection.commit()
        try:
            target.put_bytes(
                numeric_path,
                numeric,
                content_type="application/x-npz",
            )
            if target.get_bytes(numeric_path) != numeric:
                raise RuntimeError("Numeric artifact exact readback did not pass.")
            _assert_boundary_unchanged(source, pointers, phoenix2)
        finally:
            with connection.cursor() as cursor:
                _release_lock(cursor)
            connection.commit()

    return {
        "status": "repaired",
        "gate": "hosted-pre-canary-numeric-artifact-repair",
        "binaryArtifacts": 1,
        "exactReadback": True,
        "stableBoundary": True,
        "productionBackendChanged": False,
    }


@router.post("/api/internal/pumbility-pre-canary")
def run_hosted_precanary_reconciliation() -> JSONResponse:
    if os.getenv("VERCEL_ENV", "").strip().casefold() != "preview" or not _enabled(
        os.getenv(PRECANARY_DIAGNOSTIC_ENV)
    ):
        return JSONResponse(status_code=404, content={"error": "Not found."})
    try:
        return JSONResponse(status_code=200, content=_run_hosted_gate(os.environ))
    except Exception as error:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Hosted pre-canary reconciliation did not pass.",
                "diagnostic": _safe_failure_evidence(os.environ, error),
            },
        )


@router.post("/api/internal/pumbility-pre-canary/artifacts/repair")
def run_hosted_precanary_artifact_repair() -> JSONResponse:
    if (
        os.getenv("VERCEL_ENV", "").strip().casefold() != "preview"
        or not _enabled(os.getenv(PRECANARY_DIAGNOSTIC_ENV))
        or not _enabled(os.getenv(PRECANARY_ARTIFACT_REPAIR_ENV))
    ):
        return JSONResponse(status_code=404, content={"error": "Not found."})
    try:
        return JSONResponse(
            status_code=200,
            content=_repair_numeric_artifact(os.environ),
        )
    except Exception as error:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Hosted pre-canary artifact repair did not pass.",
                "diagnostic": _safe_failure_evidence(os.environ, error),
            },
        )
