from __future__ import annotations

import asyncio
import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text

from pathlib import Path

from fastapi.staticfiles import StaticFiles

from app.api.routes_api import router as api_router
from app.api.routes_pages import router as pages_router
from app.config import ConfigError, Settings, get_settings, production_problems
from app.container import Container
from app.logging_config import configure_logging, request_id_var
from app.services.cleanup_service import run_cleanup
from app.services.dataset_service import DatasetNotFound
from app.services.job_service import JobConflict
from app.services.prediction_service import IncompatibleInputError, NoModelError
from app.services.training_service import TrainingError
from app.services.weather_service import WeatherUnavailable
from app.upload import UploadError

log = logging.getLogger("wps")


def create_app(settings: Settings | None = None, container: Container | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)
    if settings.app_env == "production":
        problems = production_problems(settings)
        if problems:
            raise ConfigError("Invalid production configuration:\n- " + "\n- ".join(problems))
    c = container or Container(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if settings.database_url.startswith("sqlite") or settings.app_env != "production":
            c.init_schema()
        run_cleanup(c)  # also recovers interrupted jobs
        task = None
        if settings.cleanup_interval_minutes > 0 and not c.run_jobs_inline:
            async def loop():
                while True:
                    await asyncio.sleep(settings.cleanup_interval_minutes * 60)
                    try:
                        await asyncio.to_thread(run_cleanup, c)
                    except Exception:
                        log.exception("periodic cleanup failed")
            task = asyncio.create_task(loop())
        yield
        if task:
            task.cancel()
        c.shutdown()

    app = FastAPI(title="Waterlogging Prediction System", version="0.1.0", lifespan=lifespan)
    app.state.c = c
    origins = [o.strip() for o in settings.cors_allowed_origins.split(",") if o.strip()]
    if origins:
        from fastapi.middleware.cors import CORSMiddleware
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "DELETE"],
                           allow_headers=["Content-Type", "X-Admin-Token"], allow_credentials=False)

    @app.middleware("http")
    async def mw(request: Request, call_next):
        rid = request.headers.get("x-request-id", "")[:36] or uuid.uuid4().hex[:12]
        request_id_var.set(rid)
        resp = await call_next(request)
        resp.headers["X-Request-ID"] = rid
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault("Permissions-Policy", "geolocation=(), camera=(), microphone=()")
        if "text/html" in resp.headers.get("content-type", ""):
            resp.headers.setdefault("Content-Security-Policy",
                                    "default-src 'self'; img-src 'self' data:; "
                                    "style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
                                    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
        return resp

    def err(status, msg, code=None):
        return JSONResponse({"detail": msg, "code": code}, status_code=status)

    @app.exception_handler(UploadError)
    async def _(r, e): return err(e.status, str(e), "upload")
    @app.exception_handler(DatasetNotFound)
    async def _(r, e): return err(404, str(e), "dataset_not_found")
    @app.exception_handler(JobConflict)
    async def _(r, e): return err(409, str(e), "job_conflict")
    @app.exception_handler(NoModelError)
    async def _(r, e): return err(409, str(e), "no_model")
    @app.exception_handler(IncompatibleInputError)
    async def _(r, e): return err(422, str(e), "incompatible_input")
    @app.exception_handler(WeatherUnavailable)
    async def _(r, e): return err(503, str(e), "weather_unavailable")
    @app.exception_handler(TrainingError)
    async def _(r, e): return err(422, str(e), "training")
    @app.exception_handler(RequestValidationError)
    async def _(r, e):
        return err(422, "Invalid request: " + "; ".join(f"{'.'.join(map(str, x['loc'][1:]))}: {x['msg']}" for x in e.errors()[:5]), "validation")
    @app.exception_handler(Exception)
    async def _(r, e):
        ref = uuid.uuid4().hex[:8]
        log.exception("unhandled error ref=%s path=%s", ref, r.url.path)
        return err(500, f"Internal server error (ref {ref}).", "internal")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/health/ready")
    def ready():
        checks = {}
        try:
            with c.session() as s:
                s.execute(text("SELECT 1"))
            checks["database"] = "ok"
        except Exception:
            checks["database"] = "error"
        try:
            c.store.list("models/ACTIVE")
            checks["storage"] = "ok"
        except Exception:
            checks["storage"] = "error"
        ok = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ok" if ok else "degraded", **checks, "environment": settings.app_env},
                            status_code=200 if ok else 503)

    app.mount("/static", StaticFiles(directory=str(Path(__file__).resolve().parent / "static")), name="static")
    app.include_router(api_router)
    app.include_router(pages_router)
    return app


def app_factory():
    return create_app()
