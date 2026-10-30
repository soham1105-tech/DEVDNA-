"""DevDNA API. Run with:  uvicorn app.main:app --reload --port 8000  (OpenAPI docs at /docs)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import db as dbmod
from app.api import ai, architecture, experiments, projects, system, traces
from app.config import settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if dbmod.engine is None:
        dbmod.init_engine()
    settings.projects_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="DevDNA API",
    version=system.VERSION,
    description="AI-powered digital twin for software systems: discover, visualise, predict, experiment, compare.",
    lifespan=lifespan,
)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                   allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type"])


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "error", "message": str(exc.detail)}
    return JSONResponse(status_code=exc.status_code, content={"error": detail})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    errors = [{"field": ".".join(str(x) for x in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"error": {"code": "validation_error", "message": "Invalid request",
                                                            "fields": errors}})


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    logging.getLogger("devdna").exception("unhandled error")
    return JSONResponse(status_code=500, content={"error": {"code": "internal_error",
                                                            "message": "Unexpected server error. Check the backend logs."}})


for r in (system.router, projects.router, architecture.router, experiments.router, ai.router, traces.router):
    app.include_router(r)
