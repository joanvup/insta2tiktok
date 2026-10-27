from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db
from .config import APP_DIR, APP_TITLE, ensure_dirs
from .routers import api, pages
from .services.jobs import job_manager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; style-src 'self'; "
        "script-src 'self'; connect-src 'self'; font-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "X-Robots-Tag": "noindex, nofollow",
}


def _hostname(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = urlsplit(value if "//" in value else f"//{value}")
        return parsed.hostname
    except ValueError:
        return None


def _forbidden(code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"error": {"code": code, "message": message}},
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    ensure_dirs()
    db.init_db()
    await job_manager.start()
    logger.info("Servidor listo en http://127.0.0.1:8000")
    try:
        yield
    finally:
        await job_manager.stop()
        logger.info("Servidor detenido.")


app = FastAPI(
    title=APP_TITLE,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)


@app.middleware("http")
async def local_only_middleware(request: Request, call_next):
    host = _hostname(request.headers.get("host"))
    if host not in LOCAL_HOSTNAMES:
        return _forbidden(
            "forbidden_host",
            "Esta aplicación solo acepta peticiones desde el equipo local.",
        )

    if request.method in MUTATING_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and _hostname(origin) not in LOCAL_HOSTNAMES:
            return _forbidden(
                "forbidden_origin",
                "Origen no permitido para una petición que modifica datos.",
            )
        if request.headers.get("sec-fetch-site", "").lower() == "cross-site":
            return _forbidden(
                "forbidden_origin",
                "Petición entre sitios bloqueada.",
            )

    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    return response


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict) and "code" in detail and "message" in detail:
        error = {"code": detail["code"], "message": detail["message"]}
    else:
        error = {
            "code": "http_error",
            "message": str(detail) if detail else "No se pudo completar la petición.",
        }
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": error},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _: Request, exc: RequestValidationError
) -> JSONResponse:
    logger.info(
        "Petición no válida: %s",
        [(error.get("loc"), error.get("type")) for error in exc.errors()],
    )
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "invalid_request",
                "message": "Datos no válidos. Revisa el formulario e inténtalo de nuevo.",
            }
        },
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("Error inesperado en la API", exc_info=exc)
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "internal_error",
                "message": "Ocurrió un error inesperado. Revisa la consola del servidor.",
            }
        },
    )


app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
app.include_router(pages.router)
app.include_router(api.router)
