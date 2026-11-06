"""Passwords, sessions, CSRF, and the guards that protect org-scoped routes."""

import hmac
import secrets
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request

from roomly import db

hasher = PasswordHasher()  # argon2id with the library's defaults

# Verified when the email is unknown, so both failure paths take the same time and an
# attacker can't use response time to discover which emails exist.
_DUMMY_HASH = hasher.hash(secrets.token_urlsafe(16))

CSRF_FIELD = "csrf"
CSRF_HEADER = "X-CSRF-Token"
LOGIN_ERROR = "Email or password is incorrect"


class LoginRequired(Exception):
    """Handled in main.py: redirect the browser to this org's login page."""

    def __init__(self, slug: str):
        self.slug = slug


@dataclass(frozen=True)
class Context:
    """The organization and signed-in user for one org-scoped request."""

    org: dict
    user_id: int
    role: str
    name: str

    @property
    def org_id(self) -> int:
        return self.org["id"]

    @property
    def slug(self) -> str:
        return self.org["slug"]

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def verify_password(stored_hash: str | None, password: str) -> bool:
    try:
        hasher.verify(stored_hash or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False
    return stored_hash is not None


def csrf_token(request: Request) -> str:
    """This session's CSRF token, created on first use."""
    if CSRF_FIELD not in request.session:
        request.session[CSRF_FIELD] = secrets.token_urlsafe(32)
    return request.session[CSRF_FIELD]


async def require_csrf(request: Request) -> None:
    """Every POST must echo the session's token, as a form field or a header."""
    expected = request.session.get(CSRF_FIELD)
    form = await request.form()
    submitted = request.headers.get(CSRF_HEADER) or form.get(CSRF_FIELD)
    if (
        not expected
        or not isinstance(submitted, str)
        or not hmac.compare_digest(expected.encode(), submitted.encode())
    ):
        raise HTTPException(status_code=403, detail="Your session expired. Please try again.")


def start_session(request: Request, user: dict, org_id: int, slug: str) -> None:
    """Replace the session on login, including its CSRF token."""
    request.session.clear()
    request.session.update(
        user_id=user["id"],
        org_id=org_id,
        slug=slug,
        role=user["role"],
        name=user["name"],
        csrf=secrets.token_urlsafe(32),
    )


def find_user_by_email(org_id: int, email: str) -> dict | None:
    with db.tenant_tx(org_id) as conn:
        row = conn.execute(
            "SELECT id, name, role, password_hash FROM users WHERE email = %s",
            (email.strip().lower(),),
        ).fetchone()
    if row is None:
        return None
    return {"id": row[0], "name": row[1], "role": row[2], "password_hash": row[3]}


def resolve_org(slug: str) -> dict:
    org = db.org_by_slug(slug)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found.")
    return org


def current_context(slug: str, request: Request) -> Context:
    """Resolve /o/{slug} and check that the session belongs to that organization."""
    org = resolve_org(slug)
    request.state.org = org["slug"]
    if request.session.get("org_id") != org["id"]:
        raise LoginRequired(org["slug"])
    return Context(
        org=org,
        user_id=request.session["user_id"],
        role=request.session["role"],
        name=request.session["name"],
    )


def require_admin(ctx: Context = Depends(current_context)) -> Context:
    if not ctx.is_admin:
        raise HTTPException(status_code=403, detail="Only admins can do that.")
    return ctx
