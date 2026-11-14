"""Small, dependency-free date resolver for academic calendar questions."""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo


_MONTHS = {
    name.lower(): idx
    for idx, name in enumerate(
        ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"),
        1,
    )
}
_WEEKDAYS = {
    "monday": 0, "mon": 0, "tuesday": 1, "tue": 1, "tues": 1,
    "wednesday": 2, "wed": 2, "thursday": 3, "thu": 3, "thur": 3,
    "thurs": 3, "friday": 4, "fri": 4, "saturday": 5, "sat": 5,
    "sunday": 6, "sun": 6,
}


def _valid_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _weekday_date(reference: date, weekday: int, mode: str) -> date:
    delta = (weekday - reference.weekday()) % 7
    if mode == "next" and delta == 0:
        delta = 7
    return reference + timedelta(days=delta)


def extract_date_from_text(text: str, reference_date: date | None = None) -> str | None:
    """Resolve explicit and common relative dates to ISO dates.

    Relative weekday phrases are evaluated against India local time. ``next
    Saturday`` means the next strictly future Saturday; ``this Saturday`` can
    resolve to today when today is Saturday.
    """
    if not text:
        return None
    q = text.strip().lower()
    today = reference_date or datetime.now(ZoneInfo("Asia/Kolkata")).date()

    for word, offset in (("today", 0), ("tomorrow", 1), ("yesterday", -1)):
        if re.search(rf"\b{word}\b", q):
            return (today + timedelta(days=offset)).isoformat()

    # ISO date (YYYY-MM-DD or YYYY/MM/DD).
    match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", q)
    if match:
        parsed = _valid_date(int(match[1]), int(match[2]), int(match[3]))
        return parsed.isoformat() if parsed else None

    # Day-first numeric date.
    match = re.search(r"\b(\d{1,2})[-/](\d{1,2})[-/](20\d{2})\b", q)
    if match:
        parsed = _valid_date(int(match[3]), int(match[2]), int(match[1]))
        return parsed.isoformat() if parsed else None

    # Named month formats with an explicit year.
    month_pattern = "|".join(_MONTHS)
    match = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_pattern})\.?[,]?\s+(20\d{{2}})\b", q)
    if match:
        parsed = _valid_date(int(match[3]), _MONTHS[match[2]], int(match[1]))
        return parsed.isoformat() if parsed else None
    match = re.search(rf"\b({month_pattern})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?[,]?\s+(20\d{{2}})\b", q)
    if match:
        parsed = _valid_date(int(match[3]), _MONTHS[match[1]], int(match[2]))
        return parsed.isoformat() if parsed else None

    # Month/day without year: choose the next occurrence. This supports
    # queries such as "August 15" while not pinning to one hard-coded AY.
    match = re.search(rf"\b({month_pattern})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?\b", q)
    if match:
        month, day = _MONTHS[match[1]], int(match[2])
        candidate = _valid_date(today.year, month, day)
        if candidate and candidate < today:
            candidate = _valid_date(today.year + 1, month, day)
        return candidate.isoformat() if candidate else None
    match = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({month_pattern})\b", q)
    if match:
        month, day = _MONTHS[match[2]], int(match[1])
        candidate = _valid_date(today.year, month, day)
        if candidate and candidate < today:
            candidate = _valid_date(today.year + 1, month, day)
        return candidate.isoformat() if candidate else None

    # Relative weekdays. Avoid interpreting "working Saturdays" as a date.
    weekday_pattern = "|".join(sorted(_WEEKDAYS, key=len, reverse=True))
    match = re.search(rf"\b(next|this|coming)\s+({weekday_pattern})\b", q)
    if not match and not re.search(r"\bworking\s+saturdays?\b", q):
        match = re.search(rf"\b(?:is|on|this|coming)\s+({weekday_pattern})\b", q)
        if match:
            # A bare "is Saturday a holiday?" asks about the coming Saturday.
            mode = "this"
            return _weekday_date(today, _WEEKDAYS[match[1]], mode).isoformat()
    if match:
        mode, weekday = match.groups()
        return _weekday_date(today, _WEEKDAYS[weekday], mode).isoformat()
    return None
