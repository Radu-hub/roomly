#!/usr/bin/env python3
"""Concurrency demo (G1): fire N simultaneous bookings at one slot. Exactly one should win.

    python scripts/race.py --base-url https://<host> --org acme --n 50 [--cleanup]

Exits non-zero unless exactly one request created a booking. Needs httpx.
"""

import argparse
import asyncio
import re
import sys
from collections import Counter
from datetime import date, timedelta

import httpx

DEMO_ROOM = "Kiwi"
DAYS_TO_SEARCH = 7
CSRF = re.compile(r'name="csrf" value="([^"]+)"')


def csrf_token(html: str) -> str:
    return CSRF.search(html).group(1)


async def find_free_slot(client: httpx.AsyncClient, org: str) -> tuple[str, str]:
    """Read the day grid and return (room_id, local start) of the room's next free slot."""
    row_pattern = re.compile(
        rf'<tr id="room-\d+" data-room="{DEMO_ROOM}" data-room-id="(\d+)">(.*?)</tr>',
        re.DOTALL,
    )
    for offset in range(DAYS_TO_SEARCH):
        day = date.today() + timedelta(days=offset)
        response = await client.get(f"/o/{org}/day", params={"date": day.isoformat()})
        response.raise_for_status()
        row = row_pattern.search(response.text)
        if row is None:
            sys.exit(f"Room {DEMO_ROOM} not found. Has the demo data been seeded?")
        free = re.search(r'class="free" data-start="([^"]+)"', row.group(2))
        if free:
            return row.group(1), free.group(1)
    sys.exit(f"No free slot for {DEMO_ROOM} in the next {DAYS_TO_SEARCH} days.")


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--org", default="acme")
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument(
        "--cleanup", action="store_true", help="cancel the booking afterwards"
    )
    args = parser.parse_args()

    limits = httpx.Limits(max_connections=args.n)
    async with httpx.AsyncClient(
        base_url=args.base_url, limits=limits, timeout=30
    ) as client:
        landing = await client.get("/")
        login = await client.post(
            "/demo-login",
            data={
                "slug": args.org,
                "role": "employee",
                "csrf": csrf_token(landing.text),
            },
        )
        if login.status_code != 303:
            sys.exit(f"Demo login failed with HTTP {login.status_code}")

        page = await client.get(f"/o/{args.org}/me")
        csrf = csrf_token(page.text)
        room_id, start = await find_free_slot(client, args.org)
        form = {
            "room_id": room_id,
            "start": start,
            "duration_minutes": "30",
            "title": "Race demo",
            "csrf": csrf,
        }

        responses = await asyncio.gather(
            *[
                client.post(
                    f"/o/{args.org}/bookings",
                    data=form,
                    headers={"Accept": "application/json"},
                )
                for _ in range(args.n)
            ]
        )

        counts = Counter(response.status_code for response in responses)
        created = counts[201]
        conflicts = counts[409]
        other = args.n - created - conflicts

        print(f"Fired {args.n} concurrent booking requests for {DEMO_ROOM} at {start}")
        print(f"  ✅ 201 Created   : {created}")
        print(f"  ⛔ 409 Conflict  : {conflicts}")
        print(f"  ❗ other         : {other}")
        if other:
            print(f"     statuses: {dict(counts)}")

        if args.cleanup:
            for response in responses:
                if response.status_code == 201:
                    booking_id = response.json()["id"]
                    await client.post(
                        f"/o/{args.org}/bookings/{booking_id}/cancel",
                        data={"csrf": csrf},
                    )
                    print(f"Cleaned up: cancelled booking {booking_id}")

    if created == 1:
        print("Database guarantee held: exactly one booking exists for that slot.")
        return 0
    print(f"FAILED: expected exactly 1 booking, got {created}.")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
