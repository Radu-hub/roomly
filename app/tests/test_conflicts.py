"""What the booking dialog tells people when the room they want is taken."""

import re

from .conftest import demo_login, insert_booking, local_time

HTMX = {"HX-Request": "true"}
OPENED_AT = re.compile(r'name="opened_at" value="([^"]+)"')


def open_form(client, ids, hour=9, minute=0, room="Mango"):
    start = local_time(hour, minute).strftime("%Y-%m-%dT%H:%M")
    response = client.get(f"/o/acme/bookings/new?room_id={ids[room]}&start={start}", headers=HTMX)
    return start, response


def submit(client, ids, csrf, start, opened_at, duration=30, room="Mango"):
    form = {
        "room_id": ids[room],
        "start": start,
        "duration_minutes": duration,
        "title": "Standup",
        "csrf": csrf,
        "opened_at": opened_at,
    }
    return client.post("/o/acme/bookings", data=form, headers=HTMX)


def book_as_ben(app_conn, ids, start_hour, end_hour, end_minute=0):
    insert_booking(
        app_conn,
        ids["acme"],
        ids["Mango"],
        ids["ben@acme.test"],
        local_time(start_hour),
        local_time(end_hour, end_minute),
        title="Planning",
    )


def test_someone_booking_while_the_form_is_open_says_just_booked(client, app_conn, ids):
    csrf = demo_login(client)
    start, form = open_form(client, ids)
    opened_at = OPENED_AT.search(form.text).group(1)

    book_as_ben(app_conn, ids, 9, 9, 30)
    response = submit(client, ids, csrf, start, opened_at)

    assert response.status_code == 409
    assert "Someone else just booked this room. Ben (“Planning”) has it for 09:00–09:30." in (
        response.text
    )
    assert "Pick another time" in response.text  # nothing left to book, so no form
    assert 'name="title"' not in response.text


def test_overlapping_an_older_booking_says_already_booked(client, app_conn, ids):
    book_as_ben(app_conn, ids, 10, 11)
    csrf = demo_login(client)
    start, form = open_form(client, ids, hour=9)
    opened_at = OPENED_AT.search(form.text).group(1)

    response = submit(client, ids, csrf, start, opened_at, duration=120)

    assert response.status_code == 409
    assert "This room is already booked for 10:00–11:00 by Ben (“Planning”)." in response.text
    # 9:00 is still free, so the form comes back offering only what fits.
    assert 'name="title"' in response.text
    assert 'value="60" selected' in response.text


def test_form_only_offers_durations_that_fit(client, app_conn, ids):
    book_as_ben(app_conn, ids, 10, 11)
    demo_login(client)
    _, form = open_form(client, ids, hour=9)

    assert re.findall(r'<option value="(\d+)"', form.text) == ["30", "60"]
    assert "booked again from 10:00, so you can have it for up to 60 minutes" in form.text


def test_form_stops_at_the_end_of_the_working_day(client, ids):
    demo_login(client)
    _, form = open_form(client, ids, hour=17)

    assert re.findall(r'<option value="(\d+)"', form.text) == ["30", "60"]
    assert "The working day ends at 18:00." in form.text


def test_opening_a_slot_taken_since_the_grid_loaded_says_so_at_once(client, app_conn, ids):
    demo_login(client)
    book_as_ben(app_conn, ids, 9, 10)  # after the grid showed 9:00 as free

    _, response = open_form(client, ids, hour=9)

    assert response.status_code == 409
    assert response.headers["HX-Retarget"] == f"#room-{ids['Mango']}"
    assert "Someone else just booked this room. Ben (“Planning”) has it for 09:00–10:00." in (
        response.text
    )
    assert "Planning" in response.text.split("<template>")[0]  # the refreshed row shows it


def test_booking_the_same_slot_twice_says_you_already_have_it(client, ids):
    csrf = demo_login(client)
    start, form = open_form(client, ids)
    opened_at = OPENED_AT.search(form.text).group(1)

    assert submit(client, ids, csrf, start, opened_at).status_code == 201
    response = submit(client, ids, csrf, start, opened_at)

    assert response.status_code == 409
    assert "You already booked this room for 09:00–09:30." in response.text
