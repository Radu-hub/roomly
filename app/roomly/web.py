"""Template rendering shared by every route."""

import time
from pathlib import Path

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from roomly.auth import csrf_token

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def wants_json(request: Request) -> bool:
    return "application/json" in request.headers.get("accept", "")


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def render(request: Request, name: str, status_code: int = 200, **context) -> HTMLResponse:
    settings = request.app.state.settings
    context.update(
        csrf=csrf_token(request),
        demo_mode=settings.demo_mode,
        version=settings.app_version,
        commit=settings.git_sha[:7],
        instance=request.app.state.instance,
        rendered_ms=round((time.perf_counter() - request.state.started) * 1000),
    )
    return templates.TemplateResponse(request, name, context, status_code=status_code)
