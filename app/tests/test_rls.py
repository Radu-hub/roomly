"""Tenant isolation: one organization can't see or touch another's rows."""

from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg import errors
from psycopg_pool import ConnectionPool

from roomly import db

from .conftest import APP_URL, OWNER_URL, insert_booking, local_time, set_tenant

NY = ZoneInfo("America/New_York")


@pytest.fixture
def globex_booking(app_conn, ids):
    return insert_booking(
        app_conn,
        ids["globex"],
        ids["Boardroom"],
        ids["gary@globex.test"],
        local_time(9, tz=NY),
        local_time(10, tz=NY),
    )


@pytest.mark.parametrize("table", ["organizations", "users", "rooms", "bookings"])
def test_no_tenant_context_sees_no_rows(app_conn, globex_booking, table):
    count = app_conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]  # noqa: S608
    assert count == 0


def test_no_tenant_context_rejects_inserts(app_conn, ids):
    with pytest.raises(
        (errors.CheckViolation, errors.InsufficientPrivilege, errors.NotNullViolation)
    ):
        app_conn.execute(
            "INSERT INTO rooms (name, capacity) VALUES ('Sneaky', 2)",
        )
    with pytest.raises(
        (errors.CheckViolation, errors.InsufficientPrivilege, errors.NotNullViolation)
    ):
        app_conn.execute(
            "INSERT INTO bookings (room_id, user_id, title, during) "
            "VALUES (%s, %s, 'Sneaky', tstzrange(%s, %s))",
            (ids["Mango"], ids["alice@acme.test"], local_time(9), local_time(10)),
        )


def test_acme_cannot_select_globex_rows(app_conn, ids, globex_booking):
    with app_conn.transaction():
        set_tenant(app_conn, ids["acme"])
        assert (
            app_conn.execute("SELECT * FROM bookings WHERE id = %s", (globex_booking,)).fetchone()
            is None
        )
        assert (
            app_conn.execute("SELECT * FROM rooms WHERE id = %s", (ids["Boardroom"],)).fetchone()
            is None
        )
        assert (
            app_conn.execute(
                "SELECT * FROM users WHERE id = %s", (ids["gary@globex.test"],)
            ).fetchone()
            is None
        )
        orgs = app_conn.execute("SELECT slug FROM organizations").fetchall()
        assert orgs == [("acme",)]


def test_acme_update_of_globex_row_affects_nothing(app_conn, owner_conn, ids, globex_booking):
    with app_conn.transaction():
        set_tenant(app_conn, ids["acme"])
        result = app_conn.execute(
            "UPDATE bookings SET title = 'Hacked' WHERE id = %s", (globex_booking,)
        )
        assert result.rowcount == 0
    title = owner_conn.execute(
        "SELECT title FROM bookings WHERE id = %s", (globex_booking,)
    ).fetchone()[0]
    assert title == "Test"


def test_insert_with_another_orgs_id_is_rejected(app_conn, ids):
    with pytest.raises((errors.InsufficientPrivilege, errors.CheckViolation)):
        with app_conn.transaction():
            set_tenant(app_conn, ids["acme"])
            app_conn.execute(
                "INSERT INTO bookings (org_id, room_id, user_id, title, during) "
                "VALUES (%s, %s, %s, 'Sneaky', tstzrange(%s, %s))",
                (
                    ids["globex"],
                    ids["Boardroom"],
                    ids["gary@globex.test"],
                    local_time(9),
                    local_time(10),
                ),
            )
    with pytest.raises(errors.InsufficientPrivilege):
        with app_conn.transaction():
            set_tenant(app_conn, ids["acme"])
            app_conn.execute(
                "INSERT INTO rooms (org_id, name, capacity) VALUES (%s, 'Sneaky', 2)",
                (ids["globex"],),
            )


def test_booking_with_another_orgs_room_is_rejected(app_conn, ids):
    with pytest.raises((errors.CheckViolation, errors.ForeignKeyViolation)):
        insert_booking(
            app_conn,
            ids["acme"],
            ids["Boardroom"],
            ids["alice@acme.test"],
            local_time(9),
            local_time(10),
        )


def test_composite_fk_stops_cross_tenant_reference_even_for_the_owner(owner_conn, ids):
    """The owner bypasses RLS and the trigger still runs, so this reaches the foreign key
    only with a same-org room and another org's user."""
    with pytest.raises(errors.ForeignKeyViolation):
        owner_conn.execute(
            "INSERT INTO bookings (org_id, room_id, user_id, title, during) "
            "VALUES (%s, %s, %s, 'Cross', tstzrange(%s, %s))",
            (ids["acme"], ids["Mango"], ids["gary@globex.test"], local_time(9), local_time(10)),
        )


def test_app_role_cannot_delete(app_conn, ids, globex_booking):
    with pytest.raises(errors.InsufficientPrivilege) as exc:
        with app_conn.transaction():
            set_tenant(app_conn, ids["globex"])
            app_conn.execute("DELETE FROM bookings WHERE id = %s", (globex_booking,))
    assert exc.value.sqlstate == "42501"


def test_organizations_are_read_only_for_the_app(app_conn, ids):
    with pytest.raises(errors.InsufficientPrivilege):
        with app_conn.transaction():
            set_tenant(app_conn, ids["acme"])
            app_conn.execute("UPDATE organizations SET name = 'Hacked'")


def test_rls_self_check_is_true_for_app_and_false_for_owner(app_conn, owner_conn):
    assert app_conn.execute("SELECT rls_enforced_for_current_user()").fetchone()[0] is True
    assert owner_conn.execute("SELECT rls_enforced_for_current_user()").fetchone()[0] is False


def test_tenant_setting_does_not_persist_after_commit_on_a_pooled_connection(ids):
    """The same physical connection, reused: the second transaction must have no tenant."""
    with ConnectionPool(APP_URL, min_size=1, max_size=1, open=True) as pool:
        with pool.connection() as conn:
            first_pid = conn.info.backend_pid
            with conn.transaction():
                set_tenant(conn, ids["acme"])
                assert conn.execute("SELECT app_current_org()").fetchone()[0] == ids["acme"]
        with pool.connection() as conn:
            assert conn.info.backend_pid == first_pid
            assert conn.execute("SELECT app_current_org()").fetchone()[0] is None
            assert conn.execute("SELECT count(*) FROM rooms").fetchone()[0] == 0


def test_tenant_tx_scopes_to_one_org_and_clears_afterwards(ids):
    db.init_pool(APP_URL)
    try:
        with db.tenant_tx(ids["acme"]) as conn:
            names = {row[0] for row in conn.execute("SELECT name FROM rooms")}
        assert "Mango" in names and "Boardroom" not in names
        with db.system_conn() as conn:
            assert conn.execute("SELECT count(*) FROM rooms").fetchone()[0] == 0
    finally:
        db.close_pool()


def test_pool_refuses_connections_that_bypass_rls():
    with pytest.raises(db.RlsNotEnforced):
        with psycopg.connect(OWNER_URL) as conn:
            db._verify_rls(conn)
