"""Unit tests for pure statistics functions."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.habit_log.models import Entry
from custom_components.habit_log.stats import (
    best_streak,
    buckets,
    build_index,
    current_streak,
    entry_days,
    period_range,
    shift_anchor,
    split_seconds_by_day,
    summarize,
)

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 7, tzinfo=UTC)


def _date_entry(habit: str, day: date) -> Entry:
    return Entry(f"{habit}-{day}", habit, day, None, None, "", NOW, NOW, None)


def _timed(habit: str, start: datetime, end: datetime) -> Entry:
    return Entry(f"{habit}-{start.isoformat()}", habit, None, start, end, "", NOW, NOW, None)


def test_split_across_midnight() -> None:
    start = datetime(2026, 10, 5, 23, 0, tzinfo=NY)
    end = datetime(2026, 10, 6, 1, 30, tzinfo=NY)
    assert split_seconds_by_day(start, end, NY) == {date(2026, 10, 5): 3600, date(2026, 10, 6): 5400}


def test_split_ending_exactly_at_midnight_stays_on_one_day() -> None:
    start = datetime(2026, 10, 5, 22, 0, tzinfo=NY)
    end = datetime(2026, 10, 6, 0, 0, tzinfo=NY)
    assert split_seconds_by_day(start, end, NY) == {date(2026, 10, 5): 7200}


def test_split_dst_spring_forward_day_has_23_hours() -> None:
    # 2026-03-08 in New York has 23 hours.
    start = datetime(2026, 3, 8, 0, 0, tzinfo=NY)
    end = datetime(2026, 3, 9, 0, 0, tzinfo=NY)
    assert split_seconds_by_day(start, end, NY) == {date(2026, 3, 8): 23 * 3600}


def test_split_dst_fall_back_day_has_25_hours() -> None:
    start = datetime(2026, 11, 1, 0, 0, tzinfo=NY)
    end = datetime(2026, 11, 2, 0, 0, tzinfo=NY)
    assert split_seconds_by_day(start, end, NY) == {date(2026, 11, 1): 25 * 3600}


def test_split_multi_day() -> None:
    start = datetime(2026, 10, 1, 12, 0, tzinfo=NY)
    end = datetime(2026, 10, 3, 12, 0, tzinfo=NY)
    result = split_seconds_by_day(start, end, NY)
    assert result == {date(2026, 10, 1): 43200, date(2026, 10, 2): 86400, date(2026, 10, 3): 43200}
    assert sum(result.values()) == (end - start).total_seconds()


def test_entry_days_and_count_day() -> None:
    entry = _timed("h", datetime(2026, 10, 5, 23, 0, tzinfo=NY), datetime(2026, 10, 6, 1, 0, tzinfo=NY))
    assert entry_days(entry, NY) == [date(2026, 10, 5), date(2026, 10, 6)]
    assert entry.count_day(NY) == date(2026, 10, 5)


@pytest.mark.parametrize(
    ("week_start", "expected_start"),
    [("monday", date(2026, 10, 5)), ("sunday", date(2026, 10, 4))],
)
def test_week_range(week_start: str, expected_start: date) -> None:
    start, end = period_range("week", date(2026, 10, 7), week_start)
    assert start == expected_start
    assert end == expected_start + timedelta(days=7)


def test_month_and_year_ranges() -> None:
    assert period_range("month", date(2026, 12, 15), "monday") == (date(2026, 12, 1), date(2027, 1, 1))
    assert period_range("year", date(2026, 6, 1), "monday") == (date(2026, 1, 1), date(2027, 1, 1))
    assert period_range("all", date(2026, 6, 1), "monday") == (None, None)


def test_shift_anchor() -> None:
    assert shift_anchor("month", date(2026, 1, 31), -1) == date(2025, 12, 1)
    assert shift_anchor("month", date(2026, 11, 3), 2) == date(2027, 1, 1)
    assert shift_anchor("week", date(2026, 10, 7), -1) == date(2026, 9, 30)
    assert shift_anchor("year", date(2026, 10, 7), -3) == date(2023, 1, 1)


def test_streaks() -> None:
    days = {date(2026, 10, d) for d in (1, 2, 3, 5, 6)}
    assert best_streak(days) == 3
    assert current_streak(days, date(2026, 10, 6)) == 2
    # Today not logged yet: streak ending yesterday still counts.
    assert current_streak(days, date(2026, 10, 7)) == 2
    # Two days missed: broken.
    assert current_streak(days, date(2026, 10, 8)) == 0
    assert best_streak(set()) == 0


def test_summarize_counts_time_and_days() -> None:
    entries = [
        _date_entry("h", date(2026, 10, 5)),
        _date_entry("h", date(2026, 10, 5)),
        _date_entry("h", date(2026, 9, 30)),
        _timed("h", datetime(2026, 10, 6, 23, 0, tzinfo=NY), datetime(2026, 10, 7, 1, 0, tzinfo=NY)),
    ]
    index = build_index(entries, NY)
    week = summarize(index, "week", date(2026, 10, 7), "monday", date(2026, 10, 7))
    assert week["count"] == 3  # timed entry counted once, on its start day
    assert week["seconds"] == 7200
    assert week["active_days"] == 3  # 5th, 6th, 7th (timed entry spills into the 7th)
    month = summarize(index, "month", date(2026, 10, 7), "monday", date(2026, 10, 7))
    assert month["count"] == 3
    year = summarize(index, "year", date(2026, 10, 7), "monday", date(2026, 10, 7))
    assert year["count"] == 4
    assert year["current_streak"] == 3
    assert year["best_streak"] == 3


def test_buckets() -> None:
    index = build_index([_date_entry("h", date(2026, 2, 3)), _date_entry("h", date(2024, 7, 1))], NY)
    year = buckets(index, "year", date(2026, 5, 1), "monday")
    assert len(year) == 12
    assert year[1]["key"] == "2026-02" and year[1]["count"] == 1
    all_time = buckets(index, "all", date(2026, 5, 1), "monday")
    assert [b["key"] for b in all_time] == ["2024", "2025", "2026"]
    month = buckets(index, "month", date(2026, 2, 10), "monday")
    assert len(month) == 28
    assert buckets(build_index([], NY), "all", date(2026, 5, 1), "monday") == []


def test_since_reset_rules() -> None:
    from custom_components.habit_log.stats import since_reset

    reset = datetime(2026, 10, 5, 12, 0, tzinfo=NY)
    before_created = datetime(2026, 10, 5, 9, 0, tzinfo=NY)
    after_created = datetime(2026, 10, 5, 15, 0, tzinfo=NY)
    entries = [
        # Timed entry spanning the reset: only the 2 h after it count, and it is
        # not counted as an occurrence (it started before the reset).
        _timed("h", datetime(2026, 10, 5, 11, 0, tzinfo=NY), datetime(2026, 10, 5, 14, 0, tzinfo=NY)),
        # Entirely before: ignored.
        _timed("h", datetime(2026, 10, 4, 20, 0, tzinfo=NY), datetime(2026, 10, 4, 21, 0, tzinfo=NY)),
        # Entirely after: counted fully.
        _timed("h", datetime(2026, 10, 6, 7, 0, tzinfo=NY), datetime(2026, 10, 6, 7, 30, tzinfo=NY)),
        # Date entries: same day logged before the reset (no), after (yes), later day (yes), earlier day (no).
        Entry("d1", "h", date(2026, 10, 5), None, None, "", before_created, before_created, None),
        Entry("d2", "h", date(2026, 10, 5), None, None, "", after_created, after_created, None),
        _date_entry("h", date(2026, 10, 6)),
        _date_entry("h", date(2026, 10, 4)),
    ]
    result = since_reset(entries, reset, NY)
    assert result["seconds"] == 2 * 3600 + 1800
    assert result["count"] == 3  # 07:00 timed + d2 + Oct 6 date entry
    assert result["active_days"] == 2  # Oct 5 and Oct 6
    assert result["reset_at"] == reset.astimezone(UTC).isoformat()
    # No reset: everything (equals all time)
    everything = since_reset(entries, None, NY)
    assert everything["count"] == 7 and everything["reset_at"] is None
