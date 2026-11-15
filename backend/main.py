"""backend/main.py — Production ASGI Server Entrypoint for M.A.C.H. AI.

Multimodal Academic Cognitive Hub — Amrita Vishwa Vidyapeetham
Mounts domain API routers (auth, chat, ingestion, evaluation) and configures
CORS, rate limiting, GZip, security headers, and persistent database schemas.
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Ensure project root is in sys.path so 'backend.xxx' imports resolve cleanly
PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("mach_ai")

import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from backend.api.auth import _create_token, get_current_user, require_admin
from backend.api.auth import router as auth_router
from backend.api.chat import router as chat_router
from backend.api.evaluation import router as evaluation_router
from backend.api.ingestion import router as ingestion_router
from backend.config import settings
from backend.core.http_client import close_shared_http_client
from backend.core.intent_router import intent_router
from backend.database import init_db_schema
from backend.services.graph_service import (
    execute_graph_co_query,
    execute_graph_prereq_query,
    execute_graph_syllabus_query,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initializes polyglot schemas, indexes, intent embeddings, and closes HTTP pools on exit."""
    _insecure_secrets = {
        "mach-ai-default-jwt-secret-2026",
        "replace_with_secure_random_secret_key_in_production",
    }
    if settings.app_env.lower() == "production" and (
        settings.jwt_secret in _insecure_secrets or len(settings.jwt_secret) < 16
    ):
        logger.warning("SECURITY WARNING: Using default or weak JWT_SECRET in production. Please set a strong JWT_SECRET in .env.")

    try:
        await init_db_schema()
    except Exception as e:
        logger.warning("Database schema init notice: %s", e)

    try:
        await intent_router.initialize()
    except Exception as e:
        logger.warning("Intent router init notice: %s", e)

    yield

    try:
        await close_shared_http_client()
    except Exception as e:
        logger.warning("HTTP pool close notice: %s", e)


app = FastAPI(
    title="M.A.C.H. - Multimodal Academic Cognitive Hub",
    description="Fine-tuned Graph-RAG framework for Campus Knowledge Systems",
    lifespan=lifespan,
)

# Rate limiting
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(Exception)
async def global_unhandled_exception_handler(request: Request, exc: Exception):
    """Guards against unhandled server exceptions and returns structured JSON."""
    logger.exception("Unhandled server exception on %s %s: %s", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred. Please contact the administrator."},
    )

# Compression & CORS (disallow credentials when wildcard '*' origin is active)
app.add_middleware(GZipMiddleware, minimum_size=1000)
_origins = settings.allowed_origins_list
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=(_origins != ["*"]),
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Response-Type", "X-Message-ID"],
)


@app.middleware("http")
async def security_and_logging_middleware(request: Request, call_next):
    """Enforces production HTTP security headers and structured access logs."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"

    if response.status_code != 304:
        try:
            import http
            phrase = http.HTTPStatus(response.status_code).phrase
        except ValueError:
            phrase = ""
        client_host = request.client.host if request.client else "127.0.0.1"
        client_port = request.client.port if request.client else 0
        http_version = request.scope.get("http_version", "1.1")
        logger.info(
            '%s:%s - "%s %s HTTP/%s" %s %s',
            client_host, client_port, request.method, request.url.path, http_version, response.status_code, phrase
        )
    return response


# Mount static uploads and public frontend directories
os.makedirs(settings.images_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=settings.uploads_dir), name="static")

_frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.isdir(_frontend_dir):
    app.mount("/public", StaticFiles(directory=_frontend_dir, html=True), name="frontend")
    _icons_dir = os.path.join(_frontend_dir, "icons")
    if os.path.isdir(_icons_dir):
        app.mount("/icons", StaticFiles(directory=_icons_dir), name="icons")


@app.get("/")
async def root_redirect():
    """Convenience redirect to student/admin portal index."""
    return RedirectResponse(url="/public/index.html")


@app.api_route("/favicon.ico", methods=["GET", "HEAD"], include_in_schema=False)
async def favicon():
    favicon_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(
            favicon_path,
            headers={"Cache-Control": "public, max-age=0, must-revalidate"},
        )
    return Response(status_code=204)


# Mount modular domain routers
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(ingestion_router)
app.include_router(evaluation_router)

# Re-exports for backward-compatibility with tests and external scripts
__all__ = [
    "_create_token",
    "app",
    "execute_graph_co_query",
    "execute_graph_prereq_query",
    "execute_graph_syllabus_query",
    "get_current_user",
    "require_admin",
]

if __name__ == "__main__":
    headless = settings.headless_mode
    is_prod = settings.app_env.lower() == "production"

    if not headless and not is_prod:
        import webbrowser
        from threading import Timer

        def open_browser():
            try:
                webbrowser.open("http://localhost:8000/public/index.html")
            except Exception:
                pass

        Timer(1.5, open_browser).start()

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=(not is_prod), access_log=False, app_dir=PROJECT_ROOT)
