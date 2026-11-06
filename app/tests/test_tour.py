"""The presentation at /tour."""

from roomly.routes.tour import SLIDES

from .conftest import csrf_from, demo_login

JSON = {"Accept": "application/json"}


def test_every_slide_renders_without_signing_in(client):
    for number, (_, heading) in enumerate(SLIDES, start=1):
        response = client.get(f"/tour/{number}")
        assert response.status_code == 200, heading
        assert heading in response.text
        assert f"{number} / {len(SLIDES)}" in response.text


def test_slides_outside_the_range_are_404(client):
    assert client.get("/tour/0").status_code == 404
    assert client.get(f"/tour/{len(SLIDES) + 1}").status_code == 404


def test_tour_resumes_at_the_last_slide_viewed(client):
    assert client.get("/tour").headers["location"] == "/tour/1"
    client.get("/tour/5")
    assert client.get("/tour").headers["location"] == "/tour/5"


def test_health_slide_shows_live_database_facts(client):
    text = client.get("/tour/4").text
    assert "enforced by the database" in text
    assert "001_init" in text


def test_race_slide_offers_sign_in_until_signed_in(client):
    assert 'name="next" value="/tour/3"' in client.get("/tour/3").text
    demo_login(client)
    assert 'id="race-button"' in client.get("/tour/3").text


def test_race_slot_needs_a_session(client):
    assert client.get("/tour/race-slot", headers=JSON).status_code == 401


def test_race_slot_is_free_and_bookable_exactly_once(client):
    csrf = demo_login(client)
    slot = client.get("/tour/race-slot", headers=JSON).json()
    form = {
        "room_id": slot["room_id"],
        "start": slot["start"],
        "duration_minutes": 30,
        "title": "Race demo",
        "csrf": csrf,
    }
    assert client.post(slot["bookings_url"], data=form, headers=JSON).status_code == 201
    assert client.post(slot["bookings_url"], data=form, headers=JSON).status_code == 409


def test_demo_login_returns_to_the_tour_but_never_offsite(client):
    for target, expected in [
        ("/tour/3", "/tour/3"),
        ("https://example.com/tour/3", "/o/acme/day"),
        ("//example.com", "/o/acme/day"),
        ("/tour/3/../../admin", "/o/acme/day"),
    ]:
        response = client.post(
            "/demo-login",
            data={"slug": "acme", "role": "employee", "next": target, "csrf": csrf_from(client)},
        )
        assert response.headers["location"] == expected, target


def test_uptimes_read_naturally():
    from roomly.routes.tour import _humanize

    assert _humanize(None) == "unknown"
    assert _humanize(30) == "less than a minute"
    assert _humanize(60) == "1 minute"
    assert _humanize(7200) == "2 hours"
    assert _humanize(86400 * 3) == "3 days"
