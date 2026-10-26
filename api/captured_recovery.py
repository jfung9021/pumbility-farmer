"""Saved-input recovery controls under the existing operator authentication."""
from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse

from api.jonathan import _tier_fit_denial
from captured_run_handoff import CapturedRecoveryConflict, execute_recovery, intent_path, prepare_recovery, summary

router = APIRouter()


def _response(status: int, value: dict) -> JSONResponse:
    return JSONResponse(status_code=status, content=value, headers={"Cache-Control": "no-store"})


def _stores():
    from analysis_runtime import PrivateBlobStore, RuntimeJobStore
    return PrivateBlobStore(), RuntimeJobStore()


def _context(*args, **kwargs):
    from analysis_runtime import captured_run_recovery_context
    return captured_run_recovery_context(*args, **kwargs)


def _load_checked(store, bundle_id):
    from analysis_runtime import load_captured_recovery_inputs
    return load_captured_recovery_inputs(store, bundle_id)


def _run(operation):
    try:
        result = operation()
        return _response(409 if result.get("state") == "blocked" else 200, result)
    except ValueError:
        return _response(400, {"error": "The captured recovery request or verified input identity is invalid."})
    except CapturedRecoveryConflict as error:
        return _response(409, {"error": str(error)})
    except Exception:
        # Never expose private artifact paths, score records, or database errors.
        return _response(503, {"error": "Captured recovery could not be verified; no replacement work was released."})


@router.post("/api/jonathan/captured-recovery/prepare")
def prepare(request: Request, payload: dict):
    denial = _tier_fit_denial(request)
    if denial is not None:
        return denial
    def operation():
        store, jobs = _stores()
        return prepare_recovery(store, jobs, payload, context_loader=_context)
    return _run(operation)


@router.get("/api/jonathan/captured-recovery")
def status(request: Request, intent_id: str = Query(default="", alias="intentId")):
    denial = _tier_fit_denial(request)
    if denial is not None:
        return denial
    def operation():
        _, jobs = _stores()
        if not intent_id:
            return {"servingRevision": jobs.captured_recovery_serving_revision(), "activeJobId": jobs.active_job_id(),
                    "admission": jobs.captured_recovery_admission()}
        intent_path(intent_id)
        result = jobs.captured_recovery_status(intent_id)
        if result is None:
            raise ValueError("Unknown recovery intent.")
        return summary(result)
    return _run(operation)


@router.post("/api/jonathan/captured-recovery/execute")
def execute(request: Request, payload: dict):
    denial = _tier_fit_denial(request)
    if denial is not None:
        return denial
    def operation():
        if set(payload) != {"intentId"}:
            raise ValueError("Only the prepared recovery intent is accepted.")
        from api._shared import enqueue_analysis
        store, jobs = _stores()
        return execute_recovery(store, jobs, payload["intentId"], enqueue_analysis, load_inputs=_load_checked)
    return _run(operation)


@router.post("/api/jonathan/captured-recovery/abort")
def abort(request: Request, payload: dict):
    denial = _tier_fit_denial(request)
    if denial is not None:
        return denial
    def operation():
        if set(payload) != {"intentId", "expectedServingRevision"}:
            raise ValueError("Safe abort requires the final serving revision.")
        intent_path(payload["intentId"])
        _, jobs = _stores()
        return summary(jobs.captured_recovery_abort(payload["intentId"], payload["expectedServingRevision"]))
    return _run(operation)
