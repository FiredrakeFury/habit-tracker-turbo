"""Pure statistics functions for Habit Log.

No Home Assistant runtime state is used here so everything is unit-testable.
All day arithmetic is done on local calendar dates; time splitting uses
timezone-aware local midnights, so DST transitions are handled correctly.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .const import (
    PERIOD_ALL,
    PERIOD_DAY,
    PERIOD_MONTH,
    PERIOD_WEEK,
    PERIOD_YEAR,
    WEEK_START_SUNDAY,
)
from .models import Entry


def local_midnight(day: date, tz: ZoneInfo) -> datetime:
    """Return local midnight starting `day`, as an aware UTC datetime.

    Returned in UTC on purpose: Python compares and subtracts datetimes that
    share a tzinfo using wall-clock time, which is wrong across DST changes.
    """
    return datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)


def split_seconds_by_day(start: datetime, end: datetime, tz: ZoneInfo) -> dict[date, float]:
    """Split [start, end) into seconds per local calendar day."""
    result: dict[date, float] = {}
    start = start.astimezone(UTC)
    end = end.astimezone(UTC)
    if end <= start:
        return result
    day = start.astimezone(tz).date()
    last_day = (end - timedelta(microseconds=1)).astimezone(tz).date()
    while day <= last_day:
        day_start = max(start, local_midnight(day, tz))
        day_end = min(end, local_midnight(day + timedelta(days=1), tz))
        seconds = (day_end - day_start).total_seconds()
        if seconds > 0:
            result[day] = seconds
        day += timedelta(days=1)
    return result


def entry_days(entry: Entry, tz: ZoneInfo) -> list[date]:
    """Return every local day an entry touches (calendar display days)."""
    if entry.is_timed:
        assert entry.start is not None and entry.end is not None
        days = list(split_seconds_by_day(entry.start, entry.end, tz))
        return days or [entry.count_day(tz)]
    return [entry.count_day(tz)]


def week_start_of(day: date, week_start: str) -> date:
    """Return the first day of the week containing `day`."""
    offset = (day.weekday() + 1) % 7 if week_start == WEEK_START_SUNDAY else day.weekday()
    return day - timedelta(days=offset)


def period_range(period: str, anchor: date, week_start: str) -> tuple[date | None, date | None]:
    """Return [start, end) dates for a period containing `anchor`.

    PERIOD_ALL returns (None, None) meaning unbounded.
    """
    if period == PERIOD_DAY:
        return anchor, anchor + timedelta(days=1)
    if period == PERIOD_WEEK:
        start = week_start_of(anchor, week_start)
        return start, start + timedelta(days=7)
    if period == PERIOD_MONTH:
        start = anchor.replace(day=1)
        end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
        return start, end
    if period == PERIOD_YEAR:
        return date(anchor.year, 1, 1), date(anchor.year + 1, 1, 1)
    if period == PERIOD_ALL:
        return None, None
    raise ValueError(f"Unknown period: {period}")


def shift_anchor(period: str, anchor: date, offset: int) -> date:
    """Move an anchor date by `offset` periods (negative = past)."""
    if offset == 0 or period == PERIOD_ALL:
        return anchor
    if period == PERIOD_DAY:
        return anchor + timedelta(days=offset)
    if period == PERIOD_WEEK:
        return anchor + timedelta(weeks=offset)
    if period == PERIOD_MONTH:
        month_index = anchor.year * 12 + (anchor.month - 1) + offset
        return date(month_index // 12, month_index % 12 + 1, 1)
    if period == PERIOD_YEAR:
        return date(anchor.year + offset, 1, 1)
    raise ValueError(f"Unknown period: {period}")


def _in_range(day: date, start: date | None, end: date | None) -> bool:
    return (start is None or day >= start) and (end is None or day < end)


def current_streak(days: set[date], today: date) -> int:
    """Consecutive days ending today, or ending yesterday if today is not logged yet."""
    cursor = today if today in days else today - timedelta(days=1)
    streak = 0
    while cursor in days:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def best_streak(days: Iterable[date]) -> int:
    """Longest run of consecutive days."""
    best = run = 0
    previous: date | None = None
    for day in sorted(set(days)):
        run = run + 1 if previous is not None and (day - previous).days == 1 else 1
        best = max(best, run)
        previous = day
    return best


@dataclass(slots=True)
class HabitIndex:
    """Per-habit aggregates, precomputed once per data change."""

    count_by_day: Counter[date] = field(default_factory=Counter)
    seconds_by_day: dict[date, float] = field(default_factory=dict)
    active_days: set[date] = field(default_factory=set)
    first_day: date | None = None
    last_day: date | None = None
    entry_count: int = 0


def build_index(entries: Iterable[Entry], tz: ZoneInfo) -> HabitIndex:
    """Build aggregates for one habit's entries."""
    index = HabitIndex()
    for entry in entries:
        index.entry_count += 1
        count_day = entry.count_day(tz)
        index.count_by_day[count_day] += 1
        if entry.is_timed:
            assert entry.start is not None and entry.end is not None
            for day, seconds in split_seconds_by_day(entry.start, entry.end, tz).items():
                index.seconds_by_day[day] = index.seconds_by_day.get(day, 0.0) + seconds
                index.active_days.add(day)
        index.active_days.add(count_day)
    if index.active_days:
        index.first_day = min(index.active_days)
        index.last_day = max(index.active_days)
    return index


def summarize(
    index: HabitIndex,
    period: str,
    anchor: date,
    week_start: str,
    today: date,
) -> dict[str, Any]:
    """Summarize a habit for one period."""
    start, end = period_range(period, anchor, week_start)
    count = sum(n for d, n in index.count_by_day.items() if _in_range(d, start, end))
    seconds = sum(s for d, s in index.seconds_by_day.items() if _in_range(d, start, end))
    period_days = {d for d in index.active_days if _in_range(d, start, end)}
    return {
        "period": period,
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "count": count,
        "seconds": round(seconds, 3),
        "active_days": len(period_days),
        "best_streak_in_period": best_streak(period_days),
        "current_streak": current_streak(index.active_days, today),
        "best_streak": best_streak(index.active_days),
        "first_day": index.first_day.isoformat() if index.first_day else None,
        "last_day": index.last_day.isoformat() if index.last_day else None,
    }


def buckets(index: HabitIndex, period: str, anchor: date, week_start: str) -> list[dict[str, Any]]:
    """Break a period into sub-buckets for charts.

    week/month -> days, year -> months, all -> years, day -> the single day.
    """
    out: list[dict[str, Any]] = []

    def _bucket(key: str, start: date, end: date) -> dict[str, Any]:
        days = {d for d in index.active_days if start <= d < end}
        return {
            "key": key,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "count": sum(n for d, n in index.count_by_day.items() if start <= d < end),
            "seconds": round(sum(s for d, s in index.seconds_by_day.items() if start <= d < end), 3),
            "active_days": len(days),
        }

    if period in (PERIOD_DAY, PERIOD_WEEK, PERIOD_MONTH):
        start, end = period_range(period, anchor, week_start)
        assert start is not None and end is not None
        day = start
        while day < end:
            out.append(_bucket(day.isoformat(), day, day + timedelta(days=1)))
            day += timedelta(days=1)
    elif period == PERIOD_YEAR:
        for month in range(1, 13):
            start = date(anchor.year, month, 1)
            end = date(anchor.year + (month == 12), month % 12 + 1, 1)
            out.append(_bucket(f"{anchor.year}-{month:02d}", start, end))
    elif period == PERIOD_ALL:
        if index.first_day and index.last_day:
            for year in range(index.first_day.year, index.last_day.year + 1):
                out.append(_bucket(str(year), date(year, 1, 1), date(year + 1, 1, 1)))
    else:
        raise ValueError(f"Unknown period: {period}")
    return out


def since_reset(entries: Iterable[Entry], reset_at: datetime | None, tz: ZoneInfo) -> dict[str, Any]:
    """Totals counted from a reset moment onward (entries themselves are untouched).

    - Timed entries: only the time after the reset counts. An entry counts as
      one occurrence if it *started* at or after the reset (same rule as
      elsewhere: an entry counts once, on its start).
    - Date-based entries: count if their day is after the reset's local day, or
      on that day and logged (created) at or after the reset. Back-filling a
      day before the reset therefore never changes "since reset".
    With no reset, everything counts (equals all time).
    """
    count = 0
    seconds = 0.0
    days: set[date] = set()
    reset_utc = reset_at.astimezone(UTC) if reset_at else None
    reset_day = reset_utc.astimezone(tz).date() if reset_utc else None
    for entry in entries:
        if entry.is_timed:
            assert entry.start is not None and entry.end is not None
            start = entry.start.astimezone(UTC)
            end = entry.end.astimezone(UTC)
            if reset_utc is not None and end <= reset_utc:
                continue
            if reset_utc is None or start >= reset_utc:
                count += 1
            for day, secs in split_seconds_by_day(
                max(start, reset_utc) if reset_utc else start, end, tz
            ).items():
                seconds += secs
                days.add(day)
        else:
            assert entry.day is not None
            if reset_day is not None and (
                entry.day < reset_day
                or (entry.day == reset_day and entry.created.astimezone(UTC) < reset_utc)
            ):
                continue
            count += 1
            days.add(entry.day)
    return {
        "reset_at": reset_utc.isoformat() if reset_utc else None,
        "count": count,
        "seconds": round(seconds, 3),
        "active_days": len(days),
    }
