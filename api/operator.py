"""Default-off protected Preview operator diagnostics."""

from __future__ import annotations

import os
from typing import Mapping

from fastapi import APIRouter
from fastapi.responses import JSONResponse


router = APIRouter()
PRECANARY_DIAGNOSTIC_ENV = "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED"


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
