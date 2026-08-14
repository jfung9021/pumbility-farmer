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


@router.post("/api/internal/pumbility-pre-canary")
def run_hosted_precanary_reconciliation() -> JSONResponse:
    if os.getenv("VERCEL_ENV", "").strip().casefold() != "preview" or not _enabled(
        os.getenv(PRECANARY_DIAGNOSTIC_ENV)
    ):
        return JSONResponse(status_code=404, content={"error": "Not found."})
    try:
        return JSONResponse(status_code=200, content=_run_hosted_gate(os.environ))
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "Hosted pre-canary reconciliation did not pass."},
        )
