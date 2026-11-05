"""Safe three-calendar-month retention for published DR Audio catalog entries.

Unknown or malformed publication dates are retained (fail closed).
All comparisons use timezone-aware UTC timestamps. No I/O or deletion occurs here.
"""
from __future__ import annotations

from datetime import datetime, timezone


RETENTION_MONTHS = 3


def parse_published(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if value.tzinfo is None:
            return None
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        return None


def three_month_cutoff(as_of: datetime) -> datetime:
    if as_of.tzinfo is None:
        raise ValueError("Retention reference must have a timezone")
    now = as_of.astimezone(timezone.utc)
    absolute_month = now.year * 12 + now.month - 1 - RETENTION_MONTHS
    year, zero_month = divmod(absolute_month, 12)
    month = zero_month + 1
    # Calendar-month subtraction: 31st clamps to the previous month's last day.
    import calendar
    day = min(now.day, calendar.monthrange(year, month)[1])
    return now.replace(year=year, month=month, day=day)


def retain_entry(entry: dict, cutoff: datetime) -> bool:
    published = parse_published(entry.get("published"))
    # Preserve entries with unknown dates; never delete on malformed or missing metadata.
    return published is None or published >= cutoff


def retain_recent_entries(entries: list[dict], as_of: datetime) -> list[dict]:
    cutoff = three_month_cutoff(as_of)
    return [entry for entry in entries if retain_entry(entry, cutoff)]
