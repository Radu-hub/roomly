"""Background jobs: python -m roomly.worker

Connects as roomly_app, except for the nightly demo reset, which needs roomly_owner. Both
jobs are idempotent, so running more than one worker is harmless.
"""

import logging
import time
from datetime import UTC, date, datetime

import psycopg

from roomly import db, seed
from roomly.config import demo_password, load_settings, owner_conninfo
from roomly.logging import configure_logging, emit_metric

log = logging.getLogger("roomly.worker")

LOOP_SECONDS = 30
NO_SHOW_INTERVAL_SECONDS = 5 * 60
DEMO_RESET_HOUR_UTC = 10


def release_no_shows() -> int:
    with db.system_conn() as conn:
        released = conn.execute("SELECT release_no_shows('10 minutes')").fetchone()[0]
    log.info("Released %d no-show bookings", released)
    emit_metric("NoShowsReleased", released)
    return released


def reset_is_due(now: datetime, last_reset: date | None) -> bool:
    return now.hour >= DEMO_RESET_HOUR_UTC and last_reset != now.date()


def main() -> None:
    configure_logging()
    settings = load_settings()
    db.init_pool(settings.database_url)
    log.info(
        "Worker started",
        extra={"fields": {"no_show": settings.no_show_release, "demo_mode": settings.demo_mode}},
    )

    now = datetime.now(UTC)
    # A restart after today's reset time shouldn't wipe what visitors are doing right now.
    last_reset = now.date() if now.hour >= DEMO_RESET_HOUR_UTC else None
    next_no_show_run = 0.0

    while True:
        try:
            if settings.no_show_release and time.monotonic() >= next_no_show_run:
                release_no_shows()
                next_no_show_run = time.monotonic() + NO_SHOW_INTERVAL_SECONDS

            now = datetime.now(UTC)
            if settings.demo_mode and reset_is_due(now, last_reset):
                seed.seed(owner_conninfo(), demo_password(), reset=True)
                last_reset = now.date()
                log.info("Demo data reset")
        except psycopg.Error as exc:
            # Stay alive through a database outage, and try again on the next loop.
            log.error("Worker job failed: %s", type(exc).__name__)
        time.sleep(LOOP_SECONDS)


if __name__ == "__main__":
    main()
