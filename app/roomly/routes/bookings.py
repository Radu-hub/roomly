"""Create, view, cancel and check in to bookings, and "my bookings"."""

from datetime import datetime, timedelta
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, Form, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from psycopg.rows import dict_row

from roomly import db, timeutil
from roomly.auth import Context, current_context, require_csrf
from roomly.logging import emit_metric
from roomly.routes.grid import DURATIONS, load_grid
from roomly.web import is_htmx, render, wants_json

router = APIRouter()

BigIntId = Annotated[int, Path(gt=0, le=2**63 - 1)]
MAX_FORM_DURATION_MINUTES = 24 * 60
CHECK_IN_WINDOW = timedelta(minutes=10)


def _local_range(during, tz_name: str) -> dict:
    return {
        "start": timeutil.utc_to_local(during.lower, tz_name),
        "end": timeutil.utc_to_local(during.upper, tz_name),
    }


def _can_check_in(booking: dict, now: datetime) -> bool:
    start = booking["during"].lower
    return (
        booking["checked_in_at"] is None
        and booking["cancelled_at"] is None
        and start - CHECK_IN_WINDOW <= now <= start + CHECK_IN_WINDOW
    )


def _booking_form(request: Request, ctx: Context, room: dict, local_start: str) -> Response:
    """The booking dialog, as an HTMX fragment."""
    return render(
        request,
        "partials/booking_form.html",
        ctx=ctx,
        room=room,
        local_start=local_start,
        title="",
        duration=30,
        durations=DURATIONS,
        error=None,
    )


@router.get("/o/{slug}/bookings/new", response_class=HTMLResponse)
def new_booking(
    request: Request,
    room_id: Annotated[int, Query(gt=0, le=2**63 - 1)],
    start: str,
    ctx: Context = Depends(current_context),
) -> Response:
    with db.tenant_tx(ctx.org_id) as conn:
        row = conn.execute(
            "SELECT id, name FROM rooms WHERE id = %s AND is_active", (room_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Room not found.")
    return _booking_form(request, ctx, {"id": row[0], "name": row[1]}, start)


def _result_fragment(
    request: Request,
    ctx: Context,
    status_code: int,
    room_id: int,
    start_utc: datetime,
    local_start: str,
    title: str,
    duration: int,
    error: str | None,
) -> Response:
    """The refreshed room row, plus the dialog: emptied on success, showing the error if not."""
    day = timeutil.utc_to_local(start_utc, ctx.org["timezone"]).date()
    with db.tenant_tx(ctx.org_id) as conn:
        grid = load_grid(conn, ctx, day, room_id=room_id)
    room = grid["rows"][0] if grid["rows"] else None
    return render(
        request,
        "partials/booking_result.html",
        status_code=status_code,
        ctx=ctx,
        grid=grid,
        row=room,
        room=room,
        local_start=local_start,
        title=title,
        duration=duration,
        durations=DURATIONS,
        error=error,
    )


@router.post("/o/{slug}/bookings", dependencies=[Depends(require_csrf)])
def create_booking(
    request: Request,
    room_id: Annotated[int, Form(gt=0, le=2**63 - 1)],
    start: Annotated[str, Form()],
    duration_minutes: Annotated[int, Form()],
    title: Annotated[str, Form()],
    ctx: Context = Depends(current_context),
) -> Response:
    title = title.strip()
    error: tuple[int, str] | None = None
    start_utc: datetime | None = None
    booking_id = None
    end_utc = None

    try:
        start_utc = timeutil.parse_local_start(start, ctx.org["timezone"])
    except ValueError:
        error = (422, "That start time isn't valid.")

    if error is None and not 0 < duration_minutes <= MAX_FORM_DURATION_MINUTES:
        error = (422, "That duration isn't valid.")

    if error is None:
        end_utc = start_utc + timedelta(minutes=duration_minutes)
        try:
            with db.tenant_tx(ctx.org_id) as conn:
                # org_id comes from the column default, which reads app.org_id. The
                # constraints, the trigger and RLS decide whether this insert is allowed.
                booking_id = conn.execute(
                    """
                    INSERT INTO bookings (room_id, user_id, title, during)
                    VALUES (%s, %s, %s, tstzrange(%s, %s))
                    RETURNING id
                    """,
                    (room_id, ctx.user_id, title, start_utc, end_utc),
                ).fetchone()[0]
        except psycopg.Error as exc:
            error = db.map_db_error(exc)
            if error is None:
                raise

    if error is None:
        emit_metric("BookingCreated")
    status_code, message = error if error else (201, None)

    if wants_json(request):
        if message:
            return JSONResponse({"error": message}, status_code=status_code)
        return JSONResponse(
            {"id": booking_id, "start": start_utc.isoformat(), "end": end_utc.isoformat()},
            status_code=201,
        )

    if is_htmx(request):
        if start_utc is None:
            raise HTTPException(status_code=status_code, detail=message)
        response = _result_fragment(
            request, ctx, status_code, room_id, start_utc, start, title, duration_minutes, message
        )
        if status_code == 404:
            response.headers["HX-Reswap"] = "none"
        return response

    if message:
        raise HTTPException(status_code=status_code, detail=message)
    day = timeutil.utc_to_local(start_utc, ctx.org["timezone"]).date()
    return RedirectResponse(f"/o/{ctx.slug}/day?date={day}", status_code=303)


@router.get("/o/{slug}/bookings/{booking_id}", response_class=HTMLResponse)
def booking_detail(
    request: Request, booking_id: BigIntId, ctx: Context = Depends(current_context)
) -> Response:
    # INTENTIONALLY no org check in Python: this query filters by id only. Row-Level Security
    # makes another organization's booking invisible, so fetching one returns 404 rather
    # than its data. scripts/isolation_demo.sh demonstrates this route.
    with db.tenant_tx(ctx.org_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            booking = cur.execute(
                """
                SELECT b.*, r.name AS room_name, u.name AS booker
                FROM bookings b
                JOIN rooms r ON r.id = b.room_id
                JOIN users u ON u.id = b.user_id
                WHERE b.id = %s
                """,
                (booking_id,),
            ).fetchone()
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found.")

    now = timeutil.now_utc()
    return render(
        request,
        "pages/booking.html",
        ctx=ctx,
        booking=booking,
        when=_local_range(booking["during"], ctx.org["timezone"]),
        can_cancel=(
            booking["cancelled_at"] is None
            and booking["during"].upper > now
            and (ctx.is_admin or booking["user_id"] == ctx.user_id)
        ),
        can_check_in=booking["user_id"] == ctx.user_id and _can_check_in(booking, now),
    )


@router.post("/o/{slug}/bookings/{booking_id}/cancel", dependencies=[Depends(require_csrf)])
def cancel_booking(booking_id: BigIntId, ctx: Context = Depends(current_context)) -> Response:
    with db.tenant_tx(ctx.org_id) as conn:
        row = conn.execute(
            "SELECT user_id, cancelled_at FROM bookings WHERE id = %s FOR UPDATE", (booking_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Booking not found.")
        owner_id, cancelled_at = row

        # Tenant isolation is the database's job. Who may act inside a tenant is the app's.
        is_owner = owner_id == ctx.user_id
        if not is_owner and not ctx.is_admin:
            raise HTTPException(status_code=403, detail="You can only cancel your own bookings.")

        if cancelled_at is None:
            conn.execute(
                "UPDATE bookings SET cancelled_at = now(), cancel_reason = %s WHERE id = %s",
                ("user" if is_owner else "admin", booking_id),
            )
    return RedirectResponse(f"/o/{ctx.slug}/me", status_code=303)


@router.post("/o/{slug}/bookings/{booking_id}/check-in", dependencies=[Depends(require_csrf)])
def check_in(booking_id: BigIntId, ctx: Context = Depends(current_context)) -> Response:
    with db.tenant_tx(ctx.org_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            booking = cur.execute(
                """
                SELECT user_id, during, checked_in_at, cancelled_at
                FROM bookings WHERE id = %s FOR UPDATE
                """,
                (booking_id,),
            ).fetchone()
        if booking is None:
            raise HTTPException(status_code=404, detail="Booking not found.")
        if booking["user_id"] != ctx.user_id:
            raise HTTPException(status_code=403, detail="You can only check in to your bookings.")
        if not _can_check_in(booking, timeutil.now_utc()):
            raise HTTPException(
                status_code=422,
                detail="Check-in opens 10 minutes before the start and closes 10 minutes after.",
            )
        conn.execute("UPDATE bookings SET checked_in_at = now() WHERE id = %s", (booking_id,))
    return RedirectResponse(f"/o/{ctx.slug}/me", status_code=303)


@router.get("/o/{slug}/me", response_class=HTMLResponse)
def my_bookings(request: Request, ctx: Context = Depends(current_context)) -> Response:
    with db.tenant_tx(ctx.org_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                """
                SELECT b.id, b.title, b.during, b.checked_in_at, b.cancelled_at,
                       r.name AS room_name
                FROM bookings b JOIN rooms r ON r.id = b.room_id
                WHERE b.user_id = %s AND b.cancelled_at IS NULL AND upper(b.during) > now()
                ORDER BY lower(b.during)
                """,
                (ctx.user_id,),
            ).fetchall()

    now = timeutil.now_utc()
    bookings = [
        {
            **row,
            "when": _local_range(row["during"], ctx.org["timezone"]),
            "can_check_in": _can_check_in(row, now),
        }
        for row in rows
    ]
    return render(request, "pages/me.html", ctx=ctx, bookings=bookings)
