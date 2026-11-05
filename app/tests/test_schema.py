"""The rules the database enforces on its own, tested through the app's role."""

from datetime import UTC, datetime, timedelta

import pytest
from psycopg import errors

from .conftest import insert_booking, local_time


@pytest.fixture
def acme(ids):
    return ids["acme"], ids["Mango"], ids["alice@acme.test"]


def test_overlap_is_rejected(app_conn, acme):
    insert_booking(app_conn, *acme, local_time(9), local_time(10))
    with pytest.raises(errors.ExclusionViolation) as exc:
        insert_booking(app_conn, *acme, local_time(9, 30), local_time(10, 30))
    assert exc.value.sqlstate == "23P01"


def test_touching_ranges_are_allowed(app_conn, acme):
    insert_booking(app_conn, *acme, local_time(9), local_time(9, 30))
    insert_booking(app_conn, *acme, local_time(9, 30), local_time(10))


def test_same_slot_in_another_room_is_allowed(app_conn, acme, ids):
    org_id, _, user_id = acme
    insert_booking(app_conn, org_id, ids["Mango"], user_id, local_time(9), local_time(10))
    insert_booking(app_conn, org_id, ids["Papaya"], user_id, local_time(9), local_time(10))


def test_cancel_frees_the_slot(app_conn, acme):
    booking_id = insert_booking(app_conn, *acme, local_time(9), local_time(10))
    with app_conn.transaction():
        app_conn.execute("SELECT set_config('app.org_id', %s, true)", (str(acme[0]),))
        app_conn.execute(
            "UPDATE bookings SET cancelled_at = now(), cancel_reason = 'user' WHERE id = %s",
            (booking_id,),
        )
    insert_booking(app_conn, *acme, local_time(9), local_time(10))


@pytest.mark.parametrize(
    "start, end",
    [
        pytest.param((6, 0), (7, 0), id="before-working-hours"),
        pytest.param((17, 30), (18, 30), id="after-working-hours"),
    ],
)
def test_outside_working_hours_is_rejected(app_conn, acme, start, end):
    with pytest.raises(errors.CheckViolation) as exc:
        insert_booking(app_conn, *acme, local_time(*start), local_time(*end))
    assert exc.value.sqlstate == "23514"
    assert "working hours" in exc.value.diag.message_primary


def test_off_half_hour_is_rejected(app_conn, acme):
    with pytest.raises(errors.CheckViolation) as exc:
        insert_booking(app_conn, *acme, local_time(9, 15), local_time(10))
    assert exc.value.sqlstate == "23514"
    assert exc.value.diag.constraint_name == "on_half_hour"


def test_longer_than_four_hours_is_rejected(app_conn, acme):
    with pytest.raises(errors.CheckViolation) as exc:
        insert_booking(app_conn, *acme, local_time(9), local_time(13, 30))
    assert exc.value.diag.constraint_name == "max_four_hours"


def test_exactly_four_hours_is_allowed(app_conn, acme):
    insert_booking(app_conn, *acme, local_time(9), local_time(13))


def test_already_ended_slot_is_rejected(app_conn, acme):
    # A week ago at noon Los Angeles time: always inside working hours, always over.
    day = datetime.now(UTC).date() - timedelta(days=7)
    with pytest.raises(errors.CheckViolation) as exc:
        insert_booking(app_conn, *acme, local_time(12, day=day), local_time(13, day=day))
    assert exc.value.sqlstate == "23514"
    assert "already ended" in exc.value.diag.message_primary


def test_inactive_room_is_rejected(app_conn, owner_conn, acme):
    org_id, _, user_id = acme
    room_id = owner_conn.execute(
        "INSERT INTO rooms (org_id, name, capacity, is_active) VALUES (%s, 'Closed', 2, false) "
        "RETURNING id",
        (org_id,),
    ).fetchone()[0]
    try:
        with pytest.raises(errors.CheckViolation) as exc:
            insert_booking(app_conn, org_id, room_id, user_id, local_time(9), local_time(10))
        assert "not available" in exc.value.diag.message_primary
    finally:
        owner_conn.execute("DELETE FROM rooms WHERE id = %s", (room_id,))
