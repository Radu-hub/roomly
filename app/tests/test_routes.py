"""The HTTP behaviour users see."""

from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from roomly.main import create_app

from .conftest import (
    DEMO_PASSWORD,
    csrf_from,
    demo_login,
    insert_booking,
    local_time,
    make_settings,
)

JSON = {"Accept": "application/json"}
NY = ZoneInfo("America/New_York")


def booking_form(ids, csrf, room="Mango", hour=9, minute=0, duration=30, title="Standup"):
    start = local_time(hour, minute).strftime("%Y-%m-%dT%H:%M")
    return {
        "room_id": ids[room],
        "start": start,
        "duration_minutes": duration,
        "title": title,
        "csrf": csrf,
    }


def test_login_success(client):
    csrf = csrf_from(client, "/o/acme/login")
    response = client.post(
        "/o/acme/login",
        data={"email": "ALICE@acme.test", "password": DEMO_PASSWORD, "csrf": csrf},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/o/acme/day"
    assert client.get("/o/acme/day").status_code == 200


def test_login_failures_share_one_generic_message(client):
    csrf = csrf_from(client, "/o/acme/login")
    wrong_password = client.post(
        "/o/acme/login", data={"email": "alice@acme.test", "password": "wrong", "csrf": csrf}
    )
    unknown_email = client.post(
        "/o/acme/login", data={"email": "nobody@acme.test", "password": "wrong", "csrf": csrf}
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert "Email or password is incorrect" in wrong_password.text
    assert "Email or password is incorrect" in unknown_email.text


def test_globex_user_cannot_log_in_through_acme(client):
    csrf = csrf_from(client, "/o/acme/login")
    response = client.post(
        "/o/acme/login", data={"email": "gary@globex.test", "password": DEMO_PASSWORD, "csrf": csrf}
    )
    assert response.status_code == 401


def test_logged_out_user_is_redirected_to_login(client):
    response = client.get("/o/acme/day")
    assert response.status_code == 303
    assert response.headers["location"] == "/o/acme/login"


def test_unknown_org_is_404(client):
    assert client.get("/o/nope/login").status_code == 404


def test_landing_shows_demo_buttons(client):
    text = client.get("/").text
    assert "Acme: employee" in text and "Acme: admin" in text and "Globex: employee" in text
    assert "Demo data resets nightly." in text


def test_demo_login_is_404_when_demo_mode_is_off():
    with TestClient(create_app(make_settings(demo_mode=False)), follow_redirects=False) as client:
        text = client.get("/").text
        assert "Acme: employee" not in text
        csrf = csrf_from(client, "/o/acme/login")
        response = client.post(
            "/demo-login", data={"slug": "acme", "role": "employee", "csrf": csrf}
        )
        assert response.status_code == 404


def test_book_returns_201(client, ids):
    csrf = demo_login(client)
    response = client.post("/o/acme/bookings", data=booking_form(ids, csrf), headers=JSON)
    assert response.status_code == 201
    assert response.json()["id"] > 0


def test_double_book_returns_409_with_friendly_message(client, ids):
    csrf = demo_login(client)
    form = booking_form(ids, csrf)
    assert client.post("/o/acme/bookings", data=form, headers=JSON).status_code == 201
    response = client.post("/o/acme/bookings", data=form, headers=JSON)
    assert response.status_code == 409
    assert response.json()["error"] == "That room was just booked by someone else."


def test_rule_violations_return_422_with_readable_messages(client, ids):
    csrf = demo_login(client)
    cases = {
        "working hours": booking_form(ids, csrf, hour=20),
        "hour or half hour": booking_form(ids, csrf, minute=15),
        "at most 4 hours": booking_form(ids, csrf, duration=300),
        "title": booking_form(ids, csrf, title="   "),
        "start time": booking_form(ids, csrf) | {"start": "garbage"},
        "duration": booking_form(ids, csrf, duration=-30),
    }
    for expected, form in cases.items():
        response = client.post("/o/acme/bookings", data=form, headers=JSON)
        assert response.status_code == 422, expected
        assert expected in response.json()["error"]
        assert "constraint" not in response.json()["error"]


def test_booking_another_orgs_room_is_rejected(client, ids):
    csrf = demo_login(client)
    form = booking_form(ids, csrf, room="Boardroom")
    response = client.post("/o/acme/bookings", data=form, headers=JSON)
    assert response.status_code in (404, 422)


def test_htmx_success_returns_row_and_clears_dialog(client, ids):
    csrf = demo_login(client)
    response = client.post(
        "/o/acme/bookings", data=booking_form(ids, csrf), headers={"HX-Request": "true"}
    )
    assert response.status_code == 201
    assert f'id="room-{ids["Mango"]}"' in response.text
    assert "Standup" in response.text
    assert 'id="dialog-slot" hx-swap-oob="true"' in response.text
    assert "<dialog" not in response.text


def test_htmx_conflict_returns_row_and_error_in_dialog(client, ids):
    csrf = demo_login(client)
    form = booking_form(ids, csrf)
    client.post("/o/acme/bookings", data=form)
    response = client.post("/o/acme/bookings", data=form, headers={"HX-Request": "true"})
    assert response.status_code == 409
    assert f'id="room-{ids["Mango"]}"' in response.text
    assert "That room was just booked by someone else." in response.text
    assert "<dialog" in response.text


def test_plain_form_redirects_to_the_day(client, ids):
    csrf = demo_login(client)
    response = client.post("/o/acme/bookings", data=booking_form(ids, csrf))
    assert response.status_code == 303
    assert response.headers["location"].startswith("/o/acme/day?date=")


def test_new_booking_fragment(client, ids):
    demo_login(client)
    start = local_time(9).strftime("%Y-%m-%dT%H:%M")
    response = client.get(f"/o/acme/bookings/new?room_id={ids['Mango']}&start={start}")
    assert response.status_code == 200
    assert "Book Mango" in response.text
    assert "<html" not in response.text


def test_day_grid_shows_bookings_with_rooms_and_slots(client, app_conn, ids):
    insert_booking(
        app_conn,
        ids["acme"],
        ids["Mango"],
        ids["alice@acme.test"],
        local_time(9),
        local_time(10),
        title="Planning",
    )
    demo_login(client)
    day = local_time(9).date()
    text = client.get(f"/o/acme/day?date={day}").text
    assert 'colspan="2"' in text
    assert "Planning" in text
    assert "★ You" in text
    assert "Mango" in text and "Kiwi" in text
    assert "Boardroom" not in text
    assert "08:00" in text and "17:30" in text


def test_day_grid_rejects_bad_dates(client):
    demo_login(client)
    assert client.get("/o/acme/day?date=nonsense").status_code == 404
    assert client.get("/o/acme/day?date=0001-01-01").status_code == 404


def test_employee_cannot_cancel_someone_elses_booking(client, app_conn, ids):
    booking_id = insert_booking(
        app_conn,
        ids["acme"],
        ids["Mango"],
        ids["adam@acme.test"],
        local_time(9),
        local_time(10),
    )
    csrf = demo_login(client)
    response = client.post(f"/o/acme/bookings/{booking_id}/cancel", data={"csrf": csrf})
    assert response.status_code == 403


def test_employee_can_cancel_own_booking_and_slot_is_free_again(client, owner_conn, ids):
    csrf = demo_login(client)
    form = booking_form(ids, csrf)
    booking_id = client.post("/o/acme/bookings", data=form, headers=JSON).json()["id"]
    response = client.post(f"/o/acme/bookings/{booking_id}/cancel", data={"csrf": csrf})
    assert response.status_code == 303
    reason = owner_conn.execute(
        "SELECT cancel_reason FROM bookings WHERE id = %s", (booking_id,)
    ).fetchone()[0]
    assert reason == "user"
    assert client.post("/o/acme/bookings", data=form, headers=JSON).status_code == 201


def test_admin_can_cancel_anyones_booking(client, app_conn, owner_conn, ids):
    booking_id = insert_booking(
        app_conn,
        ids["acme"],
        ids["Mango"],
        ids["alice@acme.test"],
        local_time(9),
        local_time(10),
    )
    csrf = demo_login(client, role="admin")
    response = client.post(f"/o/acme/bookings/{booking_id}/cancel", data={"csrf": csrf})
    assert response.status_code == 303
    reason = owner_conn.execute(
        "SELECT cancel_reason FROM bookings WHERE id = %s", (booking_id,)
    ).fetchone()[0]
    assert reason == "admin"


def test_admin_pages_are_403_for_employee(client, ids):
    csrf = demo_login(client)
    assert client.get("/o/acme/admin/rooms").status_code == 403
    response = client.post("/o/acme/admin/rooms", data={"name": "X", "capacity": 2, "csrf": csrf})
    assert response.status_code == 403
    response = client.post(
        f"/o/acme/admin/rooms/{ids['Mango']}", data={"name": "X", "capacity": 2, "csrf": csrf}
    )
    assert response.status_code == 403


def test_admin_can_add_edit_and_deactivate_a_room(client, owner_conn, ids):
    csrf = demo_login(client, role="admin")
    try:
        response = client.post(
            "/o/acme/admin/rooms",
            data={"name": "Pomelo", "capacity": 3, "features": ["phone", "bogus"], "csrf": csrf},
        )
        assert response.status_code == 303
        room_id, features = owner_conn.execute(
            "SELECT id, features FROM rooms WHERE name = 'Pomelo'"
        ).fetchone()
        assert features == ["phone"]
        assert "Pomelo" in client.get("/o/acme/admin/rooms").text

        response = client.post(
            f"/o/acme/admin/rooms/{room_id}",
            data={"name": "Pomelo", "capacity": 5, "csrf": csrf},
        )
        assert response.status_code == 303
        capacity, active = owner_conn.execute(
            "SELECT capacity, is_active FROM rooms WHERE id = %s", (room_id,)
        ).fetchone()
        assert (capacity, active) == (5, False)

        duplicate = client.post(
            "/o/acme/admin/rooms", data={"name": "Pomelo", "capacity": 3, "csrf": csrf}
        )
        assert duplicate.status_code == 409
        invalid = client.post(
            "/o/acme/admin/rooms", data={"name": "Huge", "capacity": 9999, "csrf": csrf}
        )
        assert invalid.status_code == 422
    finally:
        owner_conn.execute("DELETE FROM rooms WHERE name IN ('Pomelo', 'Huge')")


def test_admin_cannot_edit_another_orgs_room(client, ids):
    csrf = demo_login(client, role="admin")
    response = client.post(
        f"/o/acme/admin/rooms/{ids['Boardroom']}",
        data={"name": "Hacked", "capacity": 2, "csrf": csrf},
    )
    assert response.status_code == 404


def test_post_without_csrf_is_403(client, ids):
    csrf = demo_login(client)
    form = booking_form(ids, csrf)
    del form["csrf"]
    assert client.post("/o/acme/bookings", data=form).status_code == 403
    form["csrf"] = "wrong-token"
    assert client.post("/o/acme/bookings", data=form).status_code == 403
    assert client.post("/logout").status_code == 403


def test_csrf_header_is_accepted(client, ids):
    csrf = demo_login(client)
    form = booking_form(ids, csrf)
    del form["csrf"]
    response = client.post("/o/acme/bookings", data=form, headers=JSON | {"X-CSRF-Token": csrf})
    assert response.status_code == 201


def test_cross_tenant_booking_fetch_is_404(client, app_conn, ids):
    globex_id = insert_booking(
        app_conn,
        ids["globex"],
        ids["Boardroom"],
        ids["gary@globex.test"],
        local_time(9, tz=NY),
        local_time(10, tz=NY),
        title="Secret merger",
    )
    demo_login(client)
    response = client.get(f"/o/acme/bookings/{globex_id}")
    assert response.status_code == 404
    assert "Secret merger" not in response.text


def test_own_booking_detail_is_visible(client, ids):
    csrf = demo_login(client)
    form = booking_form(ids, csrf, title="Visible")
    booking_id = client.post("/o/acme/bookings", data=form, headers=JSON).json()["id"]
    response = client.get(f"/o/acme/bookings/{booking_id}")
    assert response.status_code == 200
    assert "Visible" in response.text and "Alice" in response.text


def test_cross_tenant_cancel_is_404(client, app_conn, ids):
    globex_id = insert_booking(
        app_conn,
        ids["globex"],
        ids["Boardroom"],
        ids["gary@globex.test"],
        local_time(9, tz=NY),
        local_time(10, tz=NY),
    )
    csrf = demo_login(client, role="admin")
    assert (
        client.post(f"/o/acme/bookings/{globex_id}/cancel", data={"csrf": csrf}).status_code == 404
    )


def test_acme_session_on_globex_redirects_to_login(client):
    demo_login(client)
    response = client.get("/o/globex/day")
    assert response.status_code == 303
    assert response.headers["location"] == "/o/globex/login"


def test_my_bookings_lists_only_mine(client, app_conn, ids):
    insert_booking(
        app_conn,
        ids["acme"],
        ids["Mango"],
        ids["alice@acme.test"],
        local_time(9),
        local_time(10),
        title="Mine",
    )
    insert_booking(
        app_conn,
        ids["acme"],
        ids["Papaya"],
        ids["adam@acme.test"],
        local_time(9),
        local_time(10),
        title="Theirs",
    )
    demo_login(client)
    text = client.get("/o/acme/me").text
    assert "Mine" in text and "Theirs" not in text


def test_find_page_validates_and_renders(client):
    demo_login(client)
    assert client.get("/o/acme/find?capacity=0").status_code == 422
    response = client.get("/o/acme/find?capacity=500&duration=30")
    assert response.status_code == 200
    assert "Find a room now" in response.text


def test_check_in_outside_the_window_is_rejected(client, ids):
    csrf = demo_login(client)
    form = booking_form(ids, csrf)
    booking_id = client.post("/o/acme/bookings", data=form, headers=JSON).json()["id"]
    response = client.post(f"/o/acme/bookings/{booking_id}/check-in", data={"csrf": csrf})
    assert response.status_code == 422


def test_logout_clears_the_session(client):
    csrf = demo_login(client)
    assert client.post("/logout", data={"csrf": csrf}).status_code == 303
    assert client.get("/o/acme/day").status_code == 303


def test_session_cookie_is_secure_in_prod():
    with TestClient(
        create_app(make_settings(app_env="prod")),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        cookie = client.get("/").headers["set-cookie"].lower()
        assert "secure" in cookie and "samesite=lax" in cookie and "httponly" in cookie
