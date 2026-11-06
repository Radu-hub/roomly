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


def _first_clash(conn: psycopg.Connection, room_id: int, start: datetime, end: datetime):
    """The earliest active booking of this room that overlaps [start, end), or None."""
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(
            """
            SELECT b.user_id, b.title, b.during, b.created_at, u.name AS booker
            FROM bookings b JOIN users u ON u.id = b.user_id
            WHERE b.room_id = %s AND b.cancelled_at IS NULL AND b.during && tstzrange(%s, %s)
            ORDER BY lower(b.during)
            LIMIT 1
            """,
            (room_id, start, end),
        ).fetchone()


def _clash_message(clash: dict, ctx: Context, just_now: bool) -> str:
    """Say who has the room and when, and whether they got it after this user looked."""
    when = _local_range(clash["during"], ctx.org["timezone"])
    span = f"{when['start']:%H:%M}–{when['end']:%H:%M}"
    if clash["user_id"] == ctx.user_id:
        return f"You already booked this room for {span}."
    holder = f"{clash['booker'].split()[0]} (“{clash['title']}”)"
    if just_now:
        return f"Someone else just booked this room. {holder} has it for {span}."
    return (
        f"This room is already booked for {span} by {holder}. "
        "Choose a shorter booking or another time."
    )


def _form_state(conn: psycopg.Connection, ctx: Context, room_id: int, start: datetime) -> dict:
    """Which durations still fit from this start, and why the longer ones don't."""
    workday_end = conn.execute("SELECT workday_end FROM organizations").fetchone()[0]
    local_start = timeutil.utc_to_local(start, ctx.org["timezone"])
    longest = start + timedelta(minutes=max(DURATIONS))
    clash = _first_clash(conn, room_id, start, longest)

    durations = []
    for minutes in DURATIONS:
        end = start + timedelta(minutes=minutes)
        local_end = timeutil.utc_to_local(end, ctx.org["timezone"])
        fits_day = local_end.date() == local_start.date() and local_end.time() <= workday_end
        if fits_day and (clash is None or end <= clash["during"].lower):
            durations.append(minutes)

    # Both rules only get stricter as the booking gets longer, so the durations that fit are
    # always the shortest ones, and DURATIONS[len(durations)] is the first that doesn't.
    limit_note = None
    if durations and len(durations) < len(DURATIONS):
        too_long_end = start + timedelta(minutes=DURATIONS[len(durations)])
        if clash is not None and too_long_end > clash["during"].lower:
            next_start = timeutil.utc_to_local(clash["during"].lower, ctx.org["timezone"])
            limit_note = (
                f"It's booked again from {next_start:%H:%M}, "
                f"so you can have it for up to {durations[-1]} minutes."
            )
        else:
            limit_note = f"The working day ends at {workday_end:%H:%M}."
    return {
        "durations": durations,
        "limit_note": limit_note,
        "clash": clash if not durations else None,
        "opened_at": conn.execute("SELECT now()").fetchone()[0],
    }


@router.get("/o/{slug}/bookings/new", response_class=HTMLResponse)
def new_booking(
    request: Request,
    room_id: Annotated[int, Query(gt=0, le=2**63 - 1)],
    start: str,
    ctx: Context = Depends(current_context),
) -> Response:
    try:
        start_utc = timeutil.parse_local_start(start, ctx.org["timezone"])
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="That start time isn't valid.") from exc
    with db.tenant_tx(ctx.org_id) as conn:
        row = conn.execute(
            "SELECT id, name FROM rooms WHERE id = %s AND is_active", (room_id,)
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Room not found.")
        state = _form_state(conn, ctx, room_id, start_utc)

    if state["clash"] is not None:
        # The grid showed this slot as free, so whoever has it booked it after the page loaded.
        # Say so now, before the user types a title, and refresh the row to show who.
        message = _clash_message(state["clash"], ctx, just_now=True)
        response = _result_fragment(request, ctx, 409, room_id, start_utc, start, "", 30, message)
        response.headers["HX-Retarget"] = f"#room-{room_id}"
        response.headers["HX-Reswap"] = "outerHTML"
        return response

    return render(
        request,
        "partials/booking_form.html",
        ctx=ctx,
        room={"id": row[0], "name": row[1]},
        local_start=start,
        title="",
        duration=30,
        error=None,
        **state,
    )


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
        state = _form_state(conn, ctx, room_id, start_utc) if error else {}
    room = grid["rows"][0] if grid["rows"] else None
    if state and duration not in state["durations"] and state["durations"]:
        duration = state["durations"][-1]
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
        error=error,
        **state,
    )


def _explain_conflict(
    ctx: Context, room_id: int, start: datetime, end: datetime, opened_at: str | None, fallback: str
) -> str:
    """Replace the generic 409 message with who has the room, if we can still find them.

    opened_at is when the form was rendered, on the database clock that also sets
    created_at, so comparing the two tells "just booked" apart from "already booked".
    """
    try:
        seen = datetime.fromisoformat(opened_at) if opened_at else None
    except ValueError:
        seen = None
    if seen is not None and seen.tzinfo is None:
        seen = None
    if seen is None:  # the JSON API: keep the documented message
        return fallback
    with db.tenant_tx(ctx.org_id) as conn:
        clash = _first_clash(conn, room_id, start, end)
    if clash is None:  # the other booking was cancelled in the meantime
        return fallback
    return _clash_message(clash, ctx, just_now=clash["created_at"] > seen)


@router.post("/o/{slug}/bookings", dependencies=[Depends(require_csrf)])
def create_booking(
    request: Request,
    room_id: Annotated[int, Form(gt=0, le=2**63 - 1)],
    start: Annotated[str, Form()],
    duration_minutes: Annotated[int, Form()],
    title: Annotated[str, Form()],
    opened_at: Annotated[str | None, Form()] = None,
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
            if error[0] == 409:
                message = _explain_conflict(ctx, room_id, start_utc, end_utc, opened_at, error[1])
                error = (409, message)

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

    if message and start_utc is None:
        raise HTTPException(status_code=status_code, detail=message)
    day = timeutil.utc_to_local(start_utc, ctx.org["timezone"]).date()
    if message:
        # Plain forms (the Find page's "Book" button): show why, with a way to pick again.
        return render(
            request,
            "pages/error.html",
            status_code=status_code,
            message=message,
            back_url=f"/o/{ctx.slug}/day?date={day}",
            back_label="See who has which room that day",
        )
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
