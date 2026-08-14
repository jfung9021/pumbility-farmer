"""Default-off protected Preview operator diagnostics."""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from typing import Mapping
from urllib.parse import urlsplit

from fastapi import APIRouter
from fastapi.responses import JSONResponse


router = APIRouter()
PRECANARY_DIAGNOSTIC_ENV = "PUMBILITY_PRECANARY_DIAGNOSTIC_ENABLED"
PRECANARY_ARTIFACT_REPAIR_ENV = "PUMBILITY_PRECANARY_ARTIFACT_REPAIR_ENABLED"
PRECANARY_SHADOW_RESTORE_ENV = "PUMBILITY_PRECANARY_SHADOW_RESTORE_ENABLED"
_shadow_restore_environment_lock = threading.Lock()


def _enabled(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def _mutation_route_enabled(environment: Mapping[str, str]) -> bool:
    return (
        environment.get("VERCEL_ENV", "").strip().casefold() == "preview"
        and _enabled(environment.get(PRECANARY_DIAGNOSTIC_ENV))
        and _enabled(environment.get(PRECANARY_SHADOW_RESTORE_ENV))
    )


@contextmanager
def _temporary_operator_environment(values: Mapping[str, str]):
    previous = {name: os.environ.get(name) for name in values}
    try:
        os.environ.update(values)
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _validated_shadow_restore_environment(
    environment: Mapping[str, str],
) -> tuple[dict[str, object], str]:
    from pumbility_store import DEFAULT_BUCKET
    from scripts.backfill_pumbility_production import EXPECTED_PROJECT_REF
    from scripts.reconcile_pumbility_production import session_url_from_runtime
    from scripts.verify_pumbility_pre_canary import assert_pre_canary_environment

    flags = assert_pre_canary_environment(environment)
    if flags["productionBackend"] != "vercel" or flags["canonicalShadowWrites"]:
        raise RuntimeError("Hosted shadow restoration requires the strict Vercel-only state.")

    runtime_url = environment.get("PUMBILITY_DATABASE_URL", "").strip()
    session_url = session_url_from_runtime(runtime_url)
    supabase_url = urlsplit(environment.get("PUMBILITY_SUPABASE_URL", "").strip())
    if (
        supabase_url.scheme != "https"
        or supabase_url.hostname != f"{EXPECTED_PROJECT_REF}.supabase.co"
        or supabase_url.username
        or supabase_url.password
        or supabase_url.port is not None
        or supabase_url.path not in {"", "/"}
        or supabase_url.query
        or supabase_url.fragment
    ):
        raise RuntimeError("The hosted Supabase project target is not approved.")
    if len(
        environment.get("PUMBILITY_SUPABASE_SERVICE_ROLE_KEY", "").encode("utf-8")
    ) < 32:
        raise RuntimeError("The hosted Storage credential is unavailable.")
    if environment.get("PUMBILITY_STORAGE_BUCKET", "").strip() != DEFAULT_BUCKET:
        raise RuntimeError("The hosted Storage bucket is not approved.")
    if len(environment.get("BLOB_READ_WRITE_TOKEN", "").encode("utf-8")) < 32:
        raise RuntimeError("The authoritative Blob credential is unavailable.")
    return flags, session_url


def _run_shadow_restore(
    environment: Mapping[str, str], *, action: str
) -> dict[str, object]:
    from scripts.backfill_pumbility_production import (
        CONFIRMATION as BACKFILL_CONFIRMATION,
        CONFIRMATION_ENV as BACKFILL_CONFIRMATION_ENV,
        EXPECTED_PROJECT_REF,
        _assert_database_target,
        _claim_lock,
        _release_lock,
        main as backfill,
    )
    from scripts.populate_pumbility_production import (
        CONFIRMATION as POPULATION_CONFIRMATION,
        CONFIRMATION_ENV as POPULATION_CONFIRMATION_ENV,
        main as populate,
    )

    if action not in {"backfill", "populate"}:
        raise ValueError("The hosted shadow restoration action is invalid.")
    flags, session_url = _validated_shadow_restore_environment(environment)
    values = {
        "PUMBILITY_DATABASE_URL": environment["PUMBILITY_DATABASE_URL"],
        "PUMBILITY_PRODUCTION_DATABASE_URL": session_url,
        BACKFILL_CONFIRMATION_ENV: BACKFILL_CONFIRMATION,
        POPULATION_CONFIRMATION_ENV: POPULATION_CONFIRMATION,
    }

    # Environment confirmations are process-global, so serialize them locally;
    # the production advisory lock below provides cross-instance serialization.
    with _shadow_restore_environment_lock, _temporary_operator_environment(values):
        if action == "backfill":
            if backfill(["--expected-project-ref", EXPECTED_PROJECT_REF]) != 0:
                raise RuntimeError("The guarded hosted backfill plan did not pass.")
            if backfill(
                ["--expected-project-ref", EXPECTED_PROJECT_REF, "--apply"]
            ) != 0:
                raise RuntimeError("The guarded hosted backfill did not pass.")
        else:
            import psycopg

            from pumbility_store import _assert_schema

            with psycopg.connect(
                session_url, prepare_threshold=None
            ) as lock_connection:
                with lock_connection.cursor() as cursor:
                    _assert_schema(cursor)
                    _assert_database_target(cursor)
                    _claim_lock(cursor)
                lock_connection.commit()
                try:
                    if populate(["--apply"]) != 0:
                        raise RuntimeError(
                            "The guarded hosted typed population did not pass."
                        )
                finally:
                    with lock_connection.cursor() as cursor:
                        _release_lock(cursor)
                    lock_connection.commit()

    session_url = ""
    return {
        "status": "completed",
        "gate": "hosted-shadow-restoration",
        "action": action,
        "safeFlags": flags,
        "databaseTargetVerified": True,
        "advisoryLock": True,
        "stableBoundary": True,
        "idempotentAndRestartable": True,
        "typedPopulationCompleted": action == "populate",
        "productionBackendChanged": False,
    }


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


def _run_shadow_restore_route(action: str) -> JSONResponse:
    if not _mutation_route_enabled(os.environ):
        return JSONResponse(status_code=404, content={"error": "Not found."})
    try:
        return JSONResponse(
            status_code=200,
            content=_run_shadow_restore(os.environ, action=action),
        )
    except Exception as error:
        return JSONResponse(
            status_code=503,
            content={
                "error": "Hosted shadow restoration did not pass.",
                "diagnostic": _safe_failure_evidence(os.environ, error),
            },
        )


@router.post("/api/internal/pumbility-pre-canary/shadow/backfill")
def run_hosted_shadow_backfill() -> JSONResponse:
    return _run_shadow_restore_route("backfill")


@router.post("/api/internal/pumbility-pre-canary/shadow/populate")
def run_hosted_shadow_population() -> JSONResponse:
    return _run_shadow_restore_route("populate")
