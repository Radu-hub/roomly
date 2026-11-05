"""Room administration. Admin-only, and tenant-scoped by RLS like everything else."""

from typing import Annotated

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from psycopg.rows import dict_row

from roomly import db
from roomly.auth import Context, require_admin, require_csrf
from roomly.routes.grid import FEATURE_ICONS
from roomly.web import render

router = APIRouter()

BigIntId = Annotated[int, Path(gt=0, le=2**63 - 1)]
FEATURES = list(FEATURE_ICONS)


def _clean_features(features: list[str]) -> list[str]:
    return [feature for feature in FEATURES if feature in features]


@router.get("/o/{slug}/admin/rooms", response_class=HTMLResponse)
def list_rooms(request: Request, ctx: Context = Depends(require_admin)) -> Response:
    with db.tenant_tx(ctx.org_id) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rooms = cur.execute(
                "SELECT id, name, capacity, features, is_active FROM rooms ORDER BY name"
            ).fetchall()
    return render(request, "pages/admin_rooms.html", ctx=ctx, rooms=rooms, features=FEATURES)


@router.post("/o/{slug}/admin/rooms", dependencies=[Depends(require_csrf)])
def create_room(
    name: Annotated[str, Form()],
    capacity: Annotated[int, Form()],
    features: Annotated[list[str] | None, Form()] = None,
    ctx: Context = Depends(require_admin),
) -> Response:
    with db.tenant_tx(ctx.org_id) as conn:
        conn.execute(
            "INSERT INTO rooms (name, capacity, features) VALUES (%s, %s, %s)",
            (name.strip(), capacity, _clean_features(features or [])),
        )
    return RedirectResponse(f"/o/{ctx.slug}/admin/rooms", status_code=303)


@router.post("/o/{slug}/admin/rooms/{room_id}", dependencies=[Depends(require_csrf)])
def update_room(
    room_id: BigIntId,
    name: Annotated[str, Form()],
    capacity: Annotated[int, Form()],
    features: Annotated[list[str] | None, Form()] = None,
    is_active: Annotated[str | None, Form()] = None,
    ctx: Context = Depends(require_admin),
) -> Response:
    with db.tenant_tx(ctx.org_id) as conn:
        result = conn.execute(
            """
            UPDATE rooms SET name = %s, capacity = %s, features = %s, is_active = %s
            WHERE id = %s
            """,
            (
                name.strip(),
                capacity,
                _clean_features(features or []),
                is_active is not None,
                room_id,
            ),
        )
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Room not found.")
    return RedirectResponse(f"/o/{ctx.slug}/admin/rooms", status_code=303)
