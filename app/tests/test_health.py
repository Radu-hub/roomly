"""/health, /health/live and /status."""

from fastapi.testclient import TestClient

from roomly.main import create_app

from .conftest import make_settings

CLOSED_PORT_URL = "host=127.0.0.1 port=1 dbname=roomly user=roomly_app password=unused"  # noqa: S105


def test_health_ok_with_full_shape(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == "test"
    assert body["commit"] == "testsha1234"
    assert isinstance(body["uptime_seconds"], int)
    assert body["instance"] == {"id": "local", "az": "local", "type": "local"}
    database = body["checks"]["database"]
    assert database["status"] == "ok"
    assert database["latency_ms"] >= 0
    assert database["schema_version"] >= "001_init"
    assert database["rls_enforced"] is True
    assert set(body["stats"]) == {"organizations", "bookings_today", "active_bookings_now"}
    assert body["stats"]["organizations"] >= 2


def test_health_degraded_when_database_unreachable():
    app = create_app(make_settings(database_url=CLOSED_PORT_URL))
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        body = response.json()
        assert body["status"] == "degraded"
        assert body["checks"]["database"]["status"] == "degraded"
        assert body["checks"]["database"]["error"]
        # Nothing about where the database lives may leak.
        assert "127.0.0.1" not in response.text
        assert "password" not in response.text

        status_page = client.get("/status")
        assert status_page.status_code == 503
        assert "Degraded" in status_page.text


def test_live_needs_no_database():
    app = create_app(make_settings(database_url=CLOSED_PORT_URL))
    with TestClient(app) as client:
        response = client.get("/health/live")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_status_page_renders_when_healthy(client):
    response = client.get("/status")
    assert response.status_code == 200
    assert "All systems OK" in response.text


def test_footer_and_security_headers(client):
    response = client.get("/")
    assert "Served by local" in response.text
    assert "vtest (testsha)" in response.text
    assert response.headers["content-security-policy"] == "default-src 'self'"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-request-id"]


def test_request_id_is_echoed_only_if_well_formed(client):
    assert (
        client.get("/health/live", headers={"X-Request-ID": "abc-123"}).headers["x-request-id"]
        == "abc-123"
    )
    assert (
        client.get("/health/live", headers={"X-Request-ID": "bad id!"}).headers["x-request-id"]
        != "bad id!"
    )
