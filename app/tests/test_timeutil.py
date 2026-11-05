"""Slot math, especially across a daylight-saving change."""

from datetime import UTC, date, datetime, time

import pytest

from roomly import timeutil

LA = "America/Los_Angeles"
START, END = time(8), time(18)


def test_nine_am_stays_nine_am_across_dst_end():
    """US clocks went back on 2026-11-01, so 09:00 local moves one hour later in UTC."""
    before = timeutil.parse_local_start("2026-10-31T09:00", LA)
    after = timeutil.parse_local_start("2026-11-02T09:00", LA)
    assert before == datetime(2026, 10, 31, 16, 0, tzinfo=UTC)  # PDT, UTC-7
    assert after == datetime(2026, 11, 2, 17, 0, tzinfo=UTC)  # PST, UTC-8
    assert timeutil.utc_to_local(before, LA).time() == time(9)
    assert timeutil.utc_to_local(after, LA).time() == time(9)


def test_slots_keep_their_local_labels_across_dst_end():
    for day in (date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 2)):
        slots = timeutil.day_slots(day, LA, START, END)
        assert len(slots) == 20
        assert slots[0].label == "08:00"
        assert slots[2].label == "09:00"
        assert slots[-1].label == "17:30"
        assert timeutil.utc_to_local(slots[2].start, LA).time() == time(9)


def test_the_change_day_itself_is_25_hours_long():
    start, end = timeutil.day_bounds(date(2026, 11, 1), LA)
    assert (end - start).total_seconds() == 25 * 3600
    start, end = timeutil.day_bounds(date(2026, 11, 2), LA)
    assert (end - start).total_seconds() == 24 * 3600


def test_spring_forward_day_is_23_hours_long():
    start, end = timeutil.day_bounds(date(2026, 3, 8), LA)
    assert (end - start).total_seconds() == 23 * 3600


def test_slots_are_half_hours_and_contiguous():
    slots = timeutil.day_slots(date(2026, 11, 3), LA, START, END)
    for current, following in zip(slots, slots[1:], strict=False):
        assert current.end == following.start
        assert (current.end - current.start).total_seconds() == 1800


def test_floor_to_slot():
    moment = datetime(2026, 11, 3, 9, 47, 12, 5, tzinfo=UTC)
    assert timeutil.floor_to_slot(moment) == datetime(2026, 11, 3, 9, 30, tzinfo=UTC)
    moment = datetime(2026, 11, 3, 9, 12, tzinfo=UTC)
    assert timeutil.floor_to_slot(moment) == datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


@pytest.mark.parametrize("value", ["not-a-date", "", "0001-01-01T09:00", "9999-01-01T09:00"])
def test_bad_start_raises_value_error(value):
    with pytest.raises(ValueError):
        timeutil.parse_local_start(value, LA)
