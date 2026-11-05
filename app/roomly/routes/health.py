"""Liveness and readiness endpoints."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import psycopg
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout

from roomly import db

router = APIRouter()
log = logging.getLogger(__name__)

DB_CHECK_TIMEOUT_SECONDS = 2
# The check runs on its own threads, so a hung database connection can't outlast the timeout.
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="health")


def _check_database() -> tuple[dict, dict | None]:
    with db.pool.connection(timeout=DB_CHECK_TIMEOUT_SECONDS) as conn:
        with conn.transaction():
            conn.execute("SELECT set_config('statement_timeout', '2s', true)")
            started = time.perf_counter()
            conn.execute("SELECT 1").fetchone()
            latency_ms = round((time.perf_counter() - started) * 1000, 1)
            version = conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version DESC LIMIT 1"
            ).fetchone()
            rls_enforced = conn.execute("SELECT rls_enforced_for_current_user()").fetchone()[0]
            stats = conn.execute(
                "SELECT organizations, bookings_today, active_bookings_now FROM health_stats()"
            ).fetchone()

    check = {
        "status": "ok",
        "latency_ms": latency_ms,
        "schema_version": version[0] if version else None,
        "rls_enforced": rls_enforced,
    }
    if rls_enforced is not True:
        check.update(status="degraded", error="Row-Level Security is not enforced")
    return check, {
        "organizations": stats[0],
        "bookings_today": stats[1],  # counted by UTC day
        "active_bookings_now": stats[2],
    }


def build_health(request: Request) -> tuple[int, dict]:
    """Returns (HTTP status, body). Errors are generic, so no hostnames or secrets appear."""
    stats = None
    future = _executor.submit(_check_database)
    try:
        check, stats = future.result(timeout=DB_CHECK_TIMEOUT_SECONDS)
    except (PoolTimeout, FutureTimeout):
        log.error("Health check: database timed out")
        check = {"status": "degraded", "error": "database unreachable"}
    except psycopg.Error as exc:
        log.error("Health check: database error: %s", type(exc).__name__)
        check = {"status": "degraded", "error": "database error"}

    settings = request.app.state.settings
    healthy = check["status"] == "ok"
    body = {
        "status": "ok" if healthy else "degraded",
        "version": settings.app_version,
        "commit": settings.git_sha,
        "build_time": settings.build_time,
        "uptime_seconds": int(time.time() - request.app.state.started_at),
        "instance": request.app.state.instance.as_dict(),
        "checks": {"database": check},
        "stats": stats,
    }
    return (200 if healthy else 503), body


@router.get("/health")
def health(request: Request) -> JSONResponse:
    status_code, body = build_health(request)
    return JSONResponse(body, status_code=status_code, headers={"Cache-Control": "no-store"})


@router.get("/health/live")
def live() -> dict:
    return {"status": "ok"}
