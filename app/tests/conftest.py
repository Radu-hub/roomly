"""Shared fixtures. Needs a bootstrapped, migrated database (make db-bootstrap migrate).

The tests truncate bookings, so they refuse to run against production.
"""

import os
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest
from fastapi.testclient import TestClient

from roomly import seed
from roomly.config import Settings
from roomly.main import create_app

if os.environ.get("APP_ENV") == "prod" or os.environ.get("SSM_PREFIX"):
    raise RuntimeError("Refusing to run the test suite against production")

APP_URL = os.environ["DATABASE_URL"]
OWNER_URL = os.environ["MIGRATION_DATABASE_URL"]
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "test-only-demo-password")
LA = ZoneInfo("America/Los_Angeles")


def make_settings(**overrides) -> Settings:
    values = {
        "app_env": "local",
        "database_url": APP_URL,
        "session_secret": "test-only-session-secret",
        "demo_mode": True,
        "no_show_release": False,
        "app_version": "test",
        "git_sha": "testsha1234",
        "build_time": "now",
    }
    return Settings(**(values | overrides))


@pytest.fixture(scope="session", autouse=True)
def seeded_database():
    seed.seed(OWNER_URL, DEMO_PASSWORD, reset=True)


@pytest.fixture(autouse=True)
def clean_bookings():
    with psycopg.connect(OWNER_URL) as conn:
        conn.execute("TRUNCATE bookings")


@pytest.fixture(scope="session")
def ids() -> dict:
    """Ids of the seeded orgs, rooms and users, looked up as the owner."""
    result = {}
    with psycopg.connect(OWNER_URL) as conn:
        for slug, org_id in conn.execute("SELECT slug, id FROM organizations"):
            result[slug] = org_id
        for name, room_id in conn.execute("SELECT name, id FROM rooms"):
            result[name] = room_id
        for email, user_id in conn.execute("SELECT email, id FROM users"):
            result[email] = user_id
    return result


def next_workday() -> date:
    """A weekday at least a day away in every test timezone, so no slot is ever in the past."""
    day = datetime.now(UTC).date() + timedelta(days=2)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def local_time(hour: int, minute: int = 0, day: date | None = None, tz: ZoneInfo = LA) -> datetime:
    day = day or next_workday()
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=tz)


@pytest.fixture
def app_conn():
    """An autocommit connection as roomly_app, the role the web app uses."""
    with psycopg.connect(APP_URL, autocommit=True) as conn:
        yield conn


@pytest.fixture
def owner_conn():
    with psycopg.connect(OWNER_URL, autocommit=True) as conn:
        yield conn


def set_tenant(conn: psycopg.Connection, org_id: int) -> None:
    conn.execute("SELECT set_config('app.org_id', %s, true)", (str(org_id),))


def insert_booking(
    conn: psycopg.Connection, org_id: int, room_id: int, user_id: int, start, end, title="Test"
) -> int:
    """Insert in one tenant transaction, as tenant_tx would."""
    with conn.transaction():
        set_tenant(conn, org_id)
        return conn.execute(
            """
            INSERT INTO bookings (room_id, user_id, title, during)
            VALUES (%s, %s, %s, tstzrange(%s, %s)) RETURNING id
            """,
            (room_id, user_id, title, start, end),
        ).fetchone()[0]


@pytest.fixture
def client():
    with TestClient(create_app(make_settings()), follow_redirects=False) as test_client:
        yield test_client


CSRF_PATTERN = re.compile(r'name="csrf" value="([^"]+)"')


def csrf_from(client: TestClient, path: str = "/") -> str:
    return CSRF_PATTERN.search(client.get(path).text).group(1)


def demo_login(client: TestClient, slug: str = "acme", role: str = "employee") -> str:
    """Log in through the demo buttons and return the session's new CSRF token."""
    response = client.post(
        "/demo-login", data={"slug": slug, "role": role, "csrf": csrf_from(client)}
    )
    assert response.status_code == 303
    return csrf_from(client)
