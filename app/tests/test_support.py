"""Metrics, the worker's schedule, instance metadata, seed and migrate."""

import json
from datetime import UTC, date, datetime

import psycopg

from roomly import metadata, migrate, seed, worker
from roomly.logging import emit_metric

from .conftest import DEMO_PASSWORD, OWNER_URL


def test_emf_line_has_the_shape_cloudwatch_expects(capsys):
    emit_metric("BookingConflict")
    line = json.loads(capsys.readouterr().out)
    definition = line["_aws"]["CloudWatchMetrics"][0]
    assert definition["Namespace"] == "Roomly"
    assert definition["Dimensions"] == [[]]
    assert definition["Metrics"] == [{"Name": "BookingConflict", "Unit": "Count"}]
    assert line["BookingConflict"] == 1
    assert isinstance(line["_aws"]["Timestamp"], int)


def test_409_emits_the_conflict_metric(client, ids, capsys):
    from .conftest import demo_login
    from .test_routes import JSON, booking_form

    csrf = demo_login(client)
    form = booking_form(ids, csrf)
    client.post("/o/acme/bookings", data=form, headers=JSON)
    capsys.readouterr()
    client.post("/o/acme/bookings", data=form, headers=JSON)
    assert '"BookingConflict": 1' in capsys.readouterr().out


def test_reset_runs_once_per_day_after_the_reset_hour():
    today = date(2026, 11, 3)
    before = datetime(2026, 11, 3, 9, 59, tzinfo=UTC)
    after = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
    assert not worker.reset_is_due(before, None)
    assert worker.reset_is_due(after, None)
    assert worker.reset_is_due(after, date(2026, 11, 2))
    assert not worker.reset_is_due(after, today)


def test_metadata_falls_back_to_local(monkeypatch):
    def unreachable(*args, **kwargs):
        raise OSError("no route to host")

    monkeypatch.setattr(metadata.urllib.request, "urlopen", unreachable)
    assert metadata.fetch_instance_info().id == "local"


def test_seed_is_idempotent_and_reset_restores_rooms(owner_conn, ids):
    seed.seed(OWNER_URL, DEMO_PASSWORD)
    before = owner_conn.execute("SELECT count(*) FROM rooms").fetchone()[0]
    seed.seed(OWNER_URL, DEMO_PASSWORD)
    assert owner_conn.execute("SELECT count(*) FROM rooms").fetchone()[0] == before

    owner_conn.execute("UPDATE rooms SET is_active = false WHERE name = 'Mango'")
    owner_conn.execute(
        "INSERT INTO rooms (org_id, name, capacity) VALUES (%s, 'Reviewer room', 2)",
        (ids["acme"],),
    )
    seed.seed(OWNER_URL, DEMO_PASSWORD, reset=True)
    assert (
        owner_conn.execute("SELECT is_active FROM rooms WHERE name = 'Mango'").fetchone()[0] is True
    )
    assert (
        owner_conn.execute("SELECT count(*) FROM rooms WHERE name = 'Reviewer room'").fetchone()[0]
        == 0
    )


def test_migrate_has_nothing_pending_and_skips_bootstrap():
    assert migrate.pending_migrations(migrate.applied_versions(OWNER_URL)) == []
    names = [path.name for path in migrate.pending_migrations(set())]
    assert "001_init.sql" in names
    assert not any(name.startswith("000_") for name in names)


def test_demo_users_can_log_in_with_the_seeded_password():
    from argon2 import PasswordHasher

    with psycopg.connect(OWNER_URL) as conn:
        stored = conn.execute(
            "SELECT password_hash FROM users WHERE email = 'alice@acme.test'"
        ).fetchone()[0]
    assert stored.startswith("$argon2id$")
    assert PasswordHasher().verify(stored, DEMO_PASSWORD)
