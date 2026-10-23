"""FastAPI application factory for the EV charging failure diagnosis backend.

Runs as its own process (uvicorn); the React frontend talks to it only through
the /api/v1 REST endpoints documented in OpenAPI at /docs. The unrelated root
catering app (app.py / catering.db) is never imported or opened here.
"""
from __future__ import annotations

import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core import db as dbmod
from app.core.logging_setup import get_logger, setup_logging
from app.core.settings import cors_origins
from app.data.seed_rules import seed_rules_if_empty


def _seed_reference_rows(conn: sqlite3.Connection) -> None:
    from app.core.db import sync_config_tables
    sync_config_tables(conn)
    seed_rules_if_empty(conn)
    if not conn.execute("SELECT 1 FROM reviewers LIMIT 1").fetchone():
        conn.executemany(
            "INSERT INTO reviewers(reviewer_id, name, role, active) VALUES (?,?,?,?)",
            [("rev-001", "A. Analyst", "diagnosis_reviewer", 1),
             ("rev-002", "B. Engineer", "field_engineer", 1),
             ("rev-003", "C. Inactive", "diagnosis_reviewer", 0)])
        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    conn = dbmod.connect(dbmod.get_db_path())
    try:
        dbmod.init_db(conn)
        _seed_reference_rows(conn)
    finally:
        conn.close()
    yield


def create_app() -> FastAPI:
    log = get_logger("app.main")
    app = FastAPI(
        title="EV Charging Failure Diagnosis Agent API",
        version="1.0.0",
        description=(
            "Evidence-based agentic AI for EV charging failure diagnosis using "
            "continuous feedback. All session data is simulated or manually "
            "entered; every record carries a `data_origin` label. No real-world "
            "performance claims are made by this system."),
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins(),
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ---- consistent error shape -------------------------------------------
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content={"error": {
            "code": "validation_error",
            "message": "Request validation failed.",
            "details": [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "")}
                        for e in exc.errors()][:20]}})

    @app.exception_handler(StarletteHTTPException)
    async def _http_handler(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and "code" in detail:
            body = {"error": detail}
        else:
            body = {"error": {"code": f"http_{exc.status_code}",
                              "message": str(detail)[:300], "details": None}}
        return JSONResponse(status_code=exc.status_code, content=body)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"error": {
            "code": "internal_error",
            "message": "Internal server error. Check server logs (no PII logged).",
            "details": None}})

    # ---- routers under /api/v1 --------------------------------------------
    from app.routers import (apps, cases, feedback_rt, health, knowledge,
                             metrics_rt, reviews, reviewers)
    prefix = "/api/v1"
    for r in (health.router, apps.router, cases.router, feedback_rt.router,
              knowledge.router, metrics_rt.router, reviews.router, reviewers.router):
        app.include_router(r, prefix=prefix)

    evaluation_router = None
    try:  # optional until evaluation module exists
        from app.routers import evaluation as evaluation_router_mod
        evaluation_router = evaluation_router_mod.router
        app.include_router(evaluation_router, prefix=prefix)
    except ImportError:
        log.warning("evaluation router not available yet")

    return app


app = create_app()
