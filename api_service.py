"""Private-data API and Celery publisher web service for Vercel Services."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.analyze import router as analyze_router
from api.cron import router as cron_router
from api.deploy import router as deploy_router
from api.jonathan import router as jonathan_router
from api.operator import router as operator_router
from api.recommendations import router as recommendations_router
from api.tier_list import router as tier_list_router
from api.topology import router as topology_router
from pumbility_store import validate_persistence_configuration


validate_persistence_configuration()
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(analyze_router)
app.include_router(cron_router)
app.include_router(deploy_router)
app.include_router(jonathan_router)
app.include_router(operator_router)
app.include_router(recommendations_router)
app.include_router(tier_list_router)
app.include_router(topology_router)


@app.exception_handler(Exception)
async def unhandled_error(_request: Request, _error: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"error": "The analysis service failed unexpectedly."},
    )
