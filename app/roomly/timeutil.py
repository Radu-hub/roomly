"""All conversion between an organization's local time and UTC lives here.

The database stores UTC. Local time only exists at the edges: parsing a form field and
rendering a page. Slots are computed from local wall-clock times, so a 09:00 slot is 09:00
local on both sides of a daylight-saving change even though its UTC time moves.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

SLOT_MINUTES = 30
MIN_YEAR = 2000
MAX_YEAR = 2100


@dataclass(frozen=True)
class Slot:
    start: datetime  # aware, in UTC
    end: datetime  # aware, in UTC
    label: str  # local wall-clock start, e.g. "09:30"
    local_start: str  # ISO local start without an offset, e.g. "2026-11-03T09:30"


def now_utc() -> datetime:
    return datetime.now(UTC)


def local_today(tz_name: str) -> date:
    return now_utc().astimezone(ZoneInfo(tz_name)).date()


def local_to_utc(local: datetime, tz_name: str) -> datetime:
    """Attach the org's timezone to a naive local datetime and convert it to UTC."""
    return local.replace(tzinfo=ZoneInfo(tz_name)).astimezone(UTC)


def utc_to_local(moment: datetime, tz_name: str) -> datetime:
    return moment.astimezone(ZoneInfo(tz_name))


def parse_local_start(value: str, tz_name: str) -> datetime:
    """Parse the form's ISO local start time to UTC. Raises ValueError if it is malformed."""
    parsed = datetime.fromisoformat(value)
    if not MIN_YEAR <= parsed.year <= MAX_YEAR:
        raise ValueError("date out of range")
    if parsed.tzinfo is not None:
        return parsed.astimezone(UTC)
    return local_to_utc(parsed, tz_name)


def parse_day(value: str | None, tz_name: str) -> date:
    """Parse ?date=YYYY-MM-DD, defaulting to today in the org's timezone."""
    if not value:
        return local_today(tz_name)
    parsed = date.fromisoformat(value)
    if not MIN_YEAR <= parsed.year <= MAX_YEAR:
        raise ValueError("date out of range")
    return parsed


def day_bounds(day: date, tz_name: str) -> tuple[datetime, datetime]:
    """UTC start and end of a local calendar day. It can be 23 or 25 hours long."""
    start = local_to_utc(datetime.combine(day, time.min), tz_name)
    end = local_to_utc(datetime.combine(day + timedelta(days=1), time.min), tz_name)
    return start, end


def day_slots(day: date, tz_name: str, workday_start: time, workday_end: time) -> list[Slot]:
    """The bookable half-hour slots of one local working day."""
    slots = []
    current = datetime.combine(day, workday_start)
    last = datetime.combine(day, workday_end)
    while current < last:
        following = current + timedelta(minutes=SLOT_MINUTES)
        slots.append(
            Slot(
                start=local_to_utc(current, tz_name),
                end=local_to_utc(following, tz_name),
                label=current.strftime("%H:%M"),
                local_start=current.strftime("%Y-%m-%dT%H:%M"),
            )
        )
        current = following
    return slots


def floor_to_slot(moment: datetime) -> datetime:
    """Round down to the half hour that contains this moment."""
    minute = moment.minute - moment.minute % SLOT_MINUTES
    return moment.replace(minute=minute, second=0, microsecond=0)
