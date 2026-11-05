"""FastAPI app factory, middleware, error handling, and the process entry point."""

import logging
import os
import re
import secrets
import sys
import time
from contextlib import asynccontextmanager

import psycopg
import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from psycopg_pool import PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware

from roomly import db
from roomly.auth import LoginRequired
from roomly.config import Settings, load_settings
from roomly.logging import configure_logging
from roomly.metadata import InstanceInfo, fetch_instance_info
from roomly.routes import admin, bookings, grid, health, public
from roomly.web import BASE_DIR, is_htmx, render, wants_json

log = logging.getLogger("roomly")

SESSION_MAX_AGE_SECONDS = 8 * 60 * 60
REQUEST_ID_FORMAT = re.compile(r"^[A-Za-z0-9-]{1,64}$")
SECURITY_HEADERS = {
    # 'self' only works because HTMX and the CSS are served from this app. It also means no
    # inline styles or scripts anywhere in the templates.
    "Content-Security-Policy": "default-src 'self'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",
    "X-Frame-Options": "DENY",
}


def _error_response(request: Request, status_code: int, message: str) -> Response:
    if wants_json(request):
        return JSONResponse({"error": message}, status_code=status_code)
    return render(request, "pages/error.html", status_code=status_code, message=message)


def _add_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(LoginRequired)
    def login_required(request: Request, exc: LoginRequired) -> Response:
        target = f"/o/{exc.slug}/login"
        if is_htmx(request):
            return Response(status_code=200, headers={"HX-Redirect": target})
        return RedirectResponse(target, status_code=303)

    @app.exception_handler(StarletteHTTPException)
    def http_error(request: Request, exc: HTTPException) -> Response:
        return _error_response(request, exc.status_code, str(exc.detail))

    @app.exception_handler(psycopg.Error)
    def database_error(request: Request, exc: psycopg.Error) -> Response:
        mapped = db.map_db_error(exc)
        if mapped is None:
            log.error("Unexpected database error: %s", type(exc).__name__, exc_info=exc)
            return _error_response(request, 500, "Something went wrong.")
        return _error_response(request, *mapped)

    @app.exception_handler(PoolTimeout)
    def pool_timeout(request: Request, exc: PoolTimeout) -> Response:
        log.error("Timed out waiting for a database connection")
        return _error_response(request, 503, "The service is temporarily unavailable.")


def create_app(settings: Settings | None = None) -> FastAPI:
    # Here and not only in main(): each uvicorn worker is a new process that runs this factory
    # and inherits no logging setup from its parent.
    configure_logging()
    settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.instance = fetch_instance_info() if settings.is_prod else InstanceInfo()
        app.state.started_at = time.time()
        db.init_pool(settings.database_url)
        yield
        db.close_pool()

    app = FastAPI(title="Roomly", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.settings = settings

    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        request.state.started = time.perf_counter()
        request.state.org = None
        supplied_id = request.headers.get("x-request-id", "")
        request_id = supplied_id if REQUEST_ID_FORMAT.match(supplied_id) else secrets.token_hex(8)

        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
        finally:
            log.info(
                "request",
                extra={
                    "fields": {
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status": status,
                        "duration_ms": round((time.perf_counter() - request.state.started) * 1000),
                        "org": request.state.org,
                        "user_id": request.session.get("user_id"),
                        "instance_id": app.state.instance.id,
                    }
                },
            )
        response.headers["X-Request-ID"] = request_id
        response.headers.update(SECURITY_HEADERS)
        return response

    # Added last, so it runs first and request.session exists inside the middleware above.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret,
        session_cookie="roomly_session",
        max_age=SESSION_MAX_AGE_SECONDS,
        same_site="lax",
        https_only=settings.is_prod,
    )

    _add_exception_handlers(app)
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    for module in (health, public, grid, bookings, admin):
        app.include_router(module.router)
    return app


def main() -> None:
    """Entry point for the web container: python -m roomly.main [--reload]

    The RLS check runs here, before uvicorn starts its workers. If it ran inside a worker,
    uvicorn's supervisor would just restart that worker forever and the process would never
    exit. An unreachable database doesn't stop startup: /health reports 503 until it's back,
    and the pool's configure hook still rejects any connection that bypasses RLS.
    """
    configure_logging()
    settings = load_settings()
    if db.rls_startup_check(settings.database_url) is False:
        log.critical(db.RLS_REFUSAL)
        sys.exit(1)

    uvicorn.run(
        "roomly.main:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - inside the container; only Caddy can reach this port
        port=int(os.environ.get("PORT", "8000")),
        workers=None if "--reload" in sys.argv else 2,
        reload="--reload" in sys.argv,
        # Only Caddy can reach this port, so its forwarded headers can be trusted.
        forwarded_allow_ips="*",
        log_config=None,
    )


if __name__ == "__main__":
    main()
