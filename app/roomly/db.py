"""Connection pool, the tenant transaction, and mapping database errors to HTTP statuses."""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg import errors
from psycopg.conninfo import make_conninfo
from psycopg_pool import ConnectionPool

from roomly.logging import emit_metric

log = logging.getLogger(__name__)

RLS_REFUSAL = "App is connected as a role that bypasses Row-Level Security; refusing to start"

pool: ConnectionPool | None = None


class RlsNotEnforced(Exception):
    """The connected role is a superuser, has BYPASSRLS, or owns the tenant tables."""


def _with_defaults(conninfo: str) -> str:
    # UTC session timezone makes health_stats() count by UTC day and keeps timestamps in
    # one zone. connect_timeout stops a dead database from hanging a request for minutes.
    return make_conninfo(conninfo, options="-c TimeZone=UTC", connect_timeout=2)


def _verify_rls(conn: psycopg.Connection) -> None:
    """Pool `configure` hook: reject any connection that isn't subject to RLS.

    The startup check can't catch a database that was down at startup and came back with
    the wrong credentials. This does, because no such connection is ever handed out.
    """
    enforced = conn.execute("SELECT rls_enforced_for_current_user()").fetchone()[0]
    conn.commit()
    if enforced is not True:
        log.critical(RLS_REFUSAL)
        raise RlsNotEnforced(RLS_REFUSAL)


def init_pool(conninfo: str) -> ConnectionPool:
    global pool
    pool = ConnectionPool(
        _with_defaults(conninfo),
        min_size=1,
        max_size=5,
        timeout=3,
        open=False,
        configure=_verify_rls,
        # Test each connection at checkout, so one left stale by a database restart is
        # replaced quietly and doesn't fail a user's request.
        check=ConnectionPool.check_connection,
    )
    pool.open(wait=False)
    return pool


def close_pool() -> None:
    global pool
    if pool is not None:
        pool.close()
        pool = None


def rls_startup_check(conninfo: str) -> bool | None:
    """True or False if the role was checked, None if the database couldn't be asked."""
    try:
        with psycopg.connect(_with_defaults(conninfo)) as conn:
            return conn.execute("SELECT rls_enforced_for_current_user()").fetchone()[0]
    except psycopg.Error as exc:
        log.error("Startup RLS check could not run: %s", type(exc).__name__)
        return None


@contextmanager
def tenant_tx(org_id: int) -> Iterator[psycopg.Connection]:
    """A transaction in which Row-Level Security sees exactly one organization."""
    with pool.connection() as conn:
        with conn.transaction():
            # set_config(..., true) == SET LOCAL: scoped to THIS transaction only,
            # so a pooled connection never carries one tenant's id into the next request.
            # (SET LOCAL can't take bind parameters; set_config can.)
            conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org_id),))
            yield conn


@contextmanager
def system_conn() -> Iterator[psycopg.Connection]:
    """No tenant context. Only for org_by_slug, health_stats, release_no_shows,
    rls_enforced_for_current_user and SELECT 1. Tenant tables return nothing here."""
    with pool.connection() as conn:
        yield conn


def org_by_slug(slug: str) -> dict | None:
    with system_conn() as conn:
        row = conn.execute(
            "SELECT id, slug, name, timezone FROM org_by_slug(%s)", (slug,)
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "slug": row[1], "name": row[2], "timezone": row[3]}


# Readable messages for named CHECK constraints. Trigger errors already read well, so they
# use their own message. These never expose table or constraint names to the user.
CHECK_MESSAGES = {
    "on_half_hour": "Bookings must start and end on the hour or half hour.",
    "max_four_hours": "Bookings can be at most 4 hours long.",
    "range_well_formed": "That time range isn't valid.",
    "bookings_title_check": "Please enter a title of 1 to 120 characters.",
    "rooms_name_check": "Please enter a room name of 1 to 60 characters.",
    "rooms_capacity_check": "Capacity must be between 1 and 500.",
}


def map_db_error(exc: psycopg.Error) -> tuple[int, str] | None:
    """The one place database errors become (HTTP status, message). None means a real bug."""
    if isinstance(exc, errors.ExclusionViolation):
        emit_metric("BookingConflict")
        return 409, "That room was just booked by someone else."
    if isinstance(exc, errors.CheckViolation):
        emit_metric("BookingRejected")
        constraint = exc.diag.constraint_name
        if constraint is None:  # raised by the validate_booking trigger
            return 422, exc.diag.message_primary
        return 422, CHECK_MESSAGES.get(constraint, "That value isn't allowed.")
    if isinstance(exc, errors.ForeignKeyViolation):
        return 404, "Not found."
    if isinstance(exc, errors.UniqueViolation):
        return 409, "That name is already in use."
    if isinstance(exc, psycopg.DataError):
        return 422, "That value isn't valid."
    return None
