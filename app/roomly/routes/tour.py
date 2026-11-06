"""A short presentation of how Roomly is built and run, with live facts from this server."""

import time
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from roomly import auth, db, timeutil
from roomly.routes.grid import load_grid
from roomly.routes.health import build_health
from roomly.web import render

router = APIRouter()

REPO_URL = "https://github.com/Radu-hub/roomly"
SLIDES = [
    ("intro", "Roomly"),
    ("try", "Try it"),
    ("race", "Fifty people, one room"),
    ("health", "The health check"),
    ("architecture", "How a request travels"),
    ("choices", "Main choices"),
    ("extras", "The optional extras"),
    ("healing", "Self-healing"),
    ("security", "Security"),
    ("cost", "Cost"),
    ("incident", "Something that went wrong"),
    ("next", "Before real customers"),
    ("end", "Thanks"),
]
RACE_DAYS_TO_SEARCH = 7


def _signed_in_context(request: Request) -> auth.Context | None:
    """The demo session's context, if there is one. Tour pages work without it."""
    slug = request.session.get("slug")
    if slug is None:
        return None
    try:
        return auth.current_context(slug, request)
    except (auth.LoginRequired, HTTPException):
        return None


def _host_uptime_seconds() -> int | None:
    """How long this server has been up. Containers share the host's kernel, so this is the
    instance's boot time, not the container's. Absent outside Linux."""
    try:
        with open("/proc/uptime") as f:
            return int(float(f.read().split()[0]))
    except OSError:
        return None


def _humanize(seconds: int | None) -> str:
    if seconds is None:
        return "unknown"
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            count = seconds // size
            return f"{count} {unit}{'' if count == 1 else 's'}"
    return "less than a minute"


@router.get("/tour")
def tour_start(request: Request) -> Response:
    # Return to the slide you left, so "How it's built" in the header resumes the talk.
    return RedirectResponse(f"/tour/{request.session.get('tour_slide', 1)}", status_code=303)


@router.get("/tour/race-slot")
def race_slot(request: Request) -> JSONResponse:
    """The earliest free half hour in the first room, for the race button to fight over."""
    ctx = _signed_in_context(request)
    if ctx is None:
        raise HTTPException(status_code=401, detail="Sign in with a demo account first.")
    today = timeutil.local_today(ctx.org["timezone"])
    with db.tenant_tx(ctx.org_id) as conn:
        for offset in range(RACE_DAYS_TO_SEARCH):
            grid = load_grid(conn, ctx, today + timedelta(days=offset))
            for row in grid["rows"][:1]:
                for cell in row["cells"]:
                    if cell["kind"] == "free":
                        return JSONResponse(
                            {
                                "room_id": row["id"],
                                "room": row["name"],
                                "start": cell["slot"].local_start,
                                "label": f"{cell['slot'].local_start[:10]} at {cell['slot'].label}",
                                "bookings_url": f"/o/{ctx.slug}/bookings",
                            }
                        )
    raise HTTPException(status_code=404, detail="No free slot in the next week.")


@router.get("/tour/{number}", response_class=HTMLResponse)
def tour_slide(request: Request, number: int) -> Response:
    if not 1 <= number <= len(SLIDES):
        raise HTTPException(status_code=404, detail="That slide doesn't exist.")
    request.session["tour_slide"] = number
    name, heading = SLIDES[number - 1]
    status_code, health = build_health(request)
    return render(
        request,
        "pages/tour.html",
        ctx=_signed_in_context(request),
        slide=name,
        heading=heading,
        number=number,
        total=len(SLIDES),
        health=health,
        health_ok=status_code == 200,
        server_up=_humanize(_host_uptime_seconds()),
        process_up=_humanize(int(time.time() - request.app.state.started_at)),
        repo_url=REPO_URL,
    )
