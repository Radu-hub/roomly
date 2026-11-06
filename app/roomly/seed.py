"""Demo data: python -m roomly.seed [--reset]

Connects as roomly_owner. Safe to run repeatedly. --reset also clears all bookings and puts
the rooms back as seeded, which the worker does nightly so visitors start clean.
"""

import random
import sys
from datetime import date, timedelta

import psycopg
from argon2 import PasswordHasher
from psycopg import errors

from roomly.config import demo_password, owner_conninfo
from roomly.timeutil import day_slots, local_today

RANDOM_SEED = 20261103
FILL_RATIO = 0.4
DURATIONS_IN_SLOTS = [1, 2, 3, 4]  # 30 to 120 minutes
TITLES = [
    "1:1",
    "Sprint planning",
    "Design review",
    "Customer call",
    "Standup",
    "Retro",
    "Roadmap sync",
    "Interview",
    "Budget review",
    "Onboarding",
]

ORGS = [
    {
        "slug": "acme",
        "name": "Acme Corp",
        "timezone": "America/Los_Angeles",
        "users": [
            ("Alice", "alice@acme.test", "employee"),
            ("Adam", "adam@acme.test", "admin"),
            ("Ben", "ben@acme.test", "employee"),
            ("Carla", "carla@acme.test", "employee"),
            ("Dev", "dev@acme.test", "employee"),
            ("Erin", "erin@acme.test", "employee"),
        ],
        "rooms": [
            ("Mango", 6, ["projector", "video"]),
            ("Papaya", 4, ["whiteboard"]),
            ("Guava", 12, ["projector", "video", "whiteboard"]),
            ("Lychee", 2, ["phone"]),
            ("Kiwi", 8, ["video"]),
        ],
    },
    {
        "slug": "globex",
        "name": "Globex Inc",
        "timezone": "America/New_York",
        "users": [
            ("Gary", "gary@globex.test", "employee"),
            ("Grace", "grace@globex.test", "admin"),
            ("Hank", "hank@globex.test", "employee"),
            ("Iris", "iris@globex.test", "employee"),
        ],
        "rooms": [
            ("Boardroom", 12, ["projector", "video"]),
            ("Huddle", 4, ["whiteboard"]),
            ("Focus", 1, []),
        ],
    },
]


def _upsert_org(conn: psycopg.Connection, org: dict, password: str, reset: bool) -> int:
    org_id = conn.execute(
        """
        INSERT INTO organizations (slug, name, timezone) VALUES (%s, %s, %s)
        ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, timezone = EXCLUDED.timezone
        RETURNING id
        """,
        (org["slug"], org["name"], org["timezone"]),
    ).fetchone()[0]

    hasher = PasswordHasher()
    for name, email, role in org["users"]:
        conn.execute(
            """
            INSERT INTO users (org_id, email, name, role, password_hash)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (org_id, email) DO UPDATE
              SET name = EXCLUDED.name, role = EXCLUDED.role,
                  password_hash = EXCLUDED.password_hash
            """,
            (org_id, email, name, role, hasher.hash(password)),
        )

    if reset:
        # Visitors may have renamed, deactivated or added rooms. Bookings are already gone,
        # so nothing references the extra rooms.
        seeded_names = [room[0] for room in org["rooms"]]
        conn.execute(
            "DELETE FROM rooms WHERE org_id = %s AND NOT (name = ANY(%s))", (org_id, seeded_names)
        )
    for name, capacity, features in org["rooms"]:
        conn.execute(
            """
            INSERT INTO rooms (org_id, name, capacity, features) VALUES (%s, %s, %s, %s)
            ON CONFLICT (org_id, name) DO UPDATE
              SET capacity = EXCLUDED.capacity, features = EXCLUDED.features, is_active = true
            """,
            (org_id, name, capacity, features),
        )
    return org_id


def _booking_days(today: date) -> list[date]:
    """Today, then the next 4 weekdays."""
    days = [today]
    current = today
    while len(days) < 5:
        current += timedelta(days=1)
        if current.weekday() < 5:
            days.append(current)
    return days


def _seed_bookings(conn: psycopg.Connection, org_id: int, org: dict, rng: random.Random) -> int:
    workday_start, workday_end = conn.execute(
        "SELECT workday_start, workday_end FROM organizations WHERE id = %s", (org_id,)
    ).fetchone()
    rooms = conn.execute(
        "SELECT id FROM rooms WHERE org_id = %s ORDER BY name", (org_id,)
    ).fetchall()
    users = conn.execute(
        "SELECT id FROM users WHERE org_id = %s ORDER BY email", (org_id,)
    ).fetchall()

    created = 0
    tz_name = org["timezone"]
    for day in _booking_days(local_today(tz_name)):
        slots = day_slots(day, tz_name, workday_start, workday_end)
        for (room_id,) in rooms:
            index = 0
            while index < len(slots):
                if rng.random() >= FILL_RATIO:
                    index += 1
                    continue
                length = min(rng.choice(DURATIONS_IN_SLOTS), len(slots) - index)
                start, end = slots[index].start, slots[index + length - 1].end
                try:
                    # A savepoint, so one rejected slot doesn't abort the whole seed.
                    with conn.transaction():
                        conn.execute(
                            """
                            INSERT INTO bookings (org_id, room_id, user_id, title, during)
                            VALUES (%s, %s, %s, %s, tstzrange(%s, %s))
                            """,
                            (org_id, room_id, rng.choice(users)[0], rng.choice(TITLES), start, end),
                        )
                    created += 1
                except (errors.CheckViolation, errors.ExclusionViolation):
                    pass  # the trigger rejects slots that have already ended
                index += length
    return created


def seed(conninfo: str, password: str, reset: bool = False) -> None:
    rng = random.Random(RANDOM_SEED)
    with psycopg.connect(conninfo) as conn:
        if reset:
            conn.execute("TRUNCATE bookings")
        for org in ORGS:
            org_id = _upsert_org(conn, org, password, reset)
            has_bookings = conn.execute(
                "SELECT EXISTS (SELECT 1 FROM bookings WHERE org_id = %s)", (org_id,)
            ).fetchone()[0]
            if has_bookings:
                print(f"{org['slug']}: bookings already exist, leaving them")
                continue
            print(f"{org['slug']}: created {_seed_bookings(conn, org_id, org, rng)} bookings")


def main() -> None:
    seed(owner_conninfo(), demo_password(), reset="--reset" in sys.argv)


if __name__ == "__main__":
    main()
