"""The day grid and "find a room now"."""

from datetime import date, timedelta
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from psycopg.rows import dict_row

from roomly import db, timeutil
from roomly.auth import Context, current_context
from roomly.web import render

router = APIRouter()

FEATURE_ICONS = {"projector": "📽", "video": "🎥", "whiteboard": "📝", "phone": "☎"}
DURATIONS = [30, 60, 90, 120, 180, 240]


def _workday(conn: psycopg.Connection):
    # RLS limits organizations to the current tenant's row.
    return conn.execute("SELECT workday_start, workday_end FROM organizations").fetchone()


def _build_cells(room_id: int, slots: list, bookings: list[dict], user_id: int) -> list[dict]:
    """Turn one room's bookings into table cells. A booking's cell spans its slots."""
    now = timeutil.now_utc()
    room_bookings = [b for b in bookings if b["room_id"] == room_id]
    cells = []
    index = 0
    while index < len(slots):
        slot = slots[index]
        booking = next(
            (
                b
                for b in room_bookings
                if b["during"].lower < slot.end and slot.start < b["during"].upper
            ),
            None,
        )
        if booking is None:
            cells.append({"kind": "past" if slot.end <= now else "free", "slot": slot})
            index += 1
            continue

        span = 1
        while index + span < len(slots) and booking["during"].upper > slots[index + span].start:
            span += 1
        cells.append(
            {
                "kind": "booked",
                "id": booking["id"],
                "title": booking["title"],
                "first_name": booking["booker"].split()[0],
                "mine": booking["user_id"] == user_id,
                "span": span,
                "ended": booking["during"].upper <= now,
            }
        )
        index += span
    return cells


def load_grid(
    conn: psycopg.Connection, ctx: Context, day: date, room_id: int | None = None
) -> dict:
    """Everything the grid template needs. Must be called inside tenant_tx."""
    tz_name = ctx.org["timezone"]
    workday_start, workday_end = _workday(conn)
    slots = timeutil.day_slots(day, tz_name, workday_start, workday_end)
    day_start, day_end = timeutil.day_bounds(day, tz_name)

    with conn.cursor(row_factory=dict_row) as cur:
        rooms = cur.execute(
            "SELECT id, name, capacity, features FROM rooms WHERE is_active ORDER BY name"
        ).fetchall()
        bookings = cur.execute(
            """
            SELECT b.id, b.room_id, b.user_id, b.title, b.during, u.name AS booker
            FROM bookings b JOIN users u ON u.id = b.user_id
            WHERE b.cancelled_at IS NULL AND b.during && tstzrange(%(day_start)s, %(day_end)s)
            """,
            {"day_start": day_start, "day_end": day_end},
        ).fetchall()

    rows = [
        {**room, "cells": _build_cells(room["id"], slots, bookings, ctx.user_id)}
        for room in rooms
        if room_id is None or room["id"] == room_id
    ]
    return {"slots": slots, "rows": rows, "icons": FEATURE_ICONS, "day": day}


@router.get("/o/{slug}/")
def org_home(slug: str, ctx: Context = Depends(current_context)) -> Response:
    return RedirectResponse(f"/o/{ctx.slug}/day", status_code=303)


@router.get("/o/{slug}/day", response_class=HTMLResponse)
def day_grid(
    request: Request, date: str | None = None, ctx: Context = Depends(current_context)
) -> Response:
    try:
        day = timeutil.parse_day(date, ctx.org["timezone"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="That date isn't valid.") from exc

    with db.tenant_tx(ctx.org_id) as conn:
        grid = load_grid(conn, ctx, day)
    return render(
        request,
        "pages/day.html",
        ctx=ctx,
        grid=grid,
        today=timeutil.local_today(ctx.org["timezone"]),
        previous_day=day - timedelta(days=1),
        next_day=day + timedelta(days=1),
    )


@router.get("/o/{slug}/find", response_class=HTMLResponse)
def find_room(
    request: Request,
    capacity: Annotated[int, Query(ge=1, le=500)] = 1,
    duration: Annotated[int, Query(ge=30, le=240)] = 30,
    ctx: Context = Depends(current_context),
) -> Response:
    tz_name = ctx.org["timezone"]
    local_start = timeutil.floor_to_slot(timeutil.utc_to_local(timeutil.now_utc(), tz_name))
    start = local_start.astimezone(timeutil.UTC)
    end = start + timedelta(minutes=duration)

    with db.tenant_tx(ctx.org_id) as conn:
        workday_start, workday_end = _workday(conn)
        # Sent back with "Book" so a conflict can say whether someone got there first.
        opened_at = conn.execute("SELECT now()").fetchone()[0]
        with conn.cursor(row_factory=dict_row) as cur:
            rooms = cur.execute(
                """
                SELECT r.* FROM rooms r
                WHERE r.is_active AND r.capacity >= %(cap)s
                  AND NOT EXISTS (SELECT 1 FROM bookings b
                                  WHERE b.room_id = r.id AND b.cancelled_at IS NULL
                                    AND b.during && tstzrange(%(start)s, %(end)s))
                ORDER BY r.capacity, r.name
                """,
                {"cap": capacity, "start": start, "end": end},
            ).fetchall()

    local_end = timeutil.utc_to_local(end, tz_name)
    within_hours = (
        local_start.time() >= workday_start
        and local_end.time() <= workday_end
        and local_start.date() == local_end.date()
    )
    return render(
        request,
        "pages/find.html",
        ctx=ctx,
        rooms=rooms if within_hours else [],
        within_hours=within_hours,
        capacity=capacity,
        duration=duration,
        durations=DURATIONS,
        icons=FEATURE_ICONS,
        start_local=local_start.strftime("%Y-%m-%dT%H:%M"),
        opened_at=opened_at,
        start_label=local_start.strftime("%H:%M"),
        end_label=local_end.strftime("%H:%M"),
    )
