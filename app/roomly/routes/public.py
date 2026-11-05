"""Landing page, status page, and signing in and out."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from roomly import auth
from roomly.auth import require_csrf
from roomly.routes.health import build_health
from roomly.web import render

router = APIRouter()

# The seeded user behind each demo button.
DEMO_USERS = {
    ("acme", "employee"): "alice@acme.test",
    ("acme", "admin"): "adam@acme.test",
    ("globex", "employee"): "gary@globex.test",
    ("globex", "admin"): "grace@globex.test",
}


@router.get("/", response_class=HTMLResponse)
def landing(request: Request) -> Response:
    return render(request, "pages/landing.html")


@router.get("/status", response_class=HTMLResponse)
def status_page(request: Request) -> Response:
    status_code, health = build_health(request)
    return render(request, "pages/status.html", status_code=status_code, health=health)


@router.post("/demo-login", dependencies=[Depends(require_csrf)])
def demo_login(request: Request, slug: str = Form(), role: str = Form()) -> Response:
    if not request.app.state.settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found.")
    email = DEMO_USERS.get((slug, role))
    if email is None:
        raise HTTPException(status_code=404, detail="Not found.")
    org = auth.resolve_org(slug)
    user = auth.find_user_by_email(org["id"], email)
    if user is None:
        raise HTTPException(status_code=404, detail="Demo data hasn't been seeded.")
    auth.start_session(request, user, org["id"])
    return RedirectResponse(f"/o/{slug}/day", status_code=303)


@router.get("/o/{slug}/login", response_class=HTMLResponse)
def login_page(slug: str, request: Request) -> Response:
    return render(request, "pages/login.html", org=auth.resolve_org(slug))


@router.post("/o/{slug}/login", dependencies=[Depends(require_csrf)])
def login(slug: str, request: Request, email: str = Form(), password: str = Form()) -> Response:
    org = auth.resolve_org(slug)
    user = auth.find_user_by_email(org["id"], email)
    stored_hash = user["password_hash"] if user else None
    if not auth.verify_password(stored_hash, password):
        return render(request, "pages/login.html", status_code=401, org=org, error=auth.LOGIN_ERROR)
    auth.start_session(request, user, org["id"])
    return RedirectResponse(f"/o/{slug}/day", status_code=303)


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request) -> Response:
    request.session.clear()
    return RedirectResponse("/", status_code=303)
