"""Deterministic, local-timezone natural-language date and time parser."""

import re
from datetime import datetime, time, timedelta

from orion.application.productivity.contracts import ParsedDateResult
from orion.application.productivity.ports import ClockPort

_WEEKDAYS = {
    "monday": 0,
    "mon": 0,
    "tuesday": 1,
    "tue": 1,
    "tues": 1,
    "wednesday": 2,
    "wed": 2,
    "thursday": 3,
    "thu": 3,
    "thur": 3,
    "thurs": 3,
    "friday": 4,
    "fri": 4,
    "saturday": 5,
    "sat": 5,
    "sunday": 6,
    "sun": 6,
}

_NUM_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "fourteen": 14,
    "thirty": 30,
}

_TIME_RE = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", re.IGNORECASE)
_TIME_24_RE = re.compile(r"\b(?:at\s+)([01]?\d|2[0-3]):([0-5]\d)\b", re.IGNORECASE)
_IN_DAYS_RE = re.compile(
    r"\bin\s+(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fourteen|thirty)\s+days?\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def _format_display(dt: datetime, is_date_only: bool) -> str:
    date_part = dt.strftime("%a, %b %-d")
    if is_date_only:
        return date_part
    time_part = dt.strftime("%-I:%M %p")
    return f"{date_part} at {time_part}"


def parse_natural_date(expression: str, clock: ClockPort) -> ParsedDateResult:
    """Parse a natural-language date/time expression in the user's configured local timezone."""
    raw = (expression or "").strip()
    if not raw:
        return ParsedDateResult(raw_expression=raw, error="Empty date expression.")

    low = raw.lower()
    # Reject uncertain / contradictory phrasing
    if any(
        bad in low
        for bad in (
            "maybe",
            "perhaps",
            "sometime",
            "not sure",
            "uncertain",
            " or ",
            "yesterday",
            "last week",
        )
    ):
        return ParsedDateResult(
            raw_expression=raw,
            ambiguous=True,
            error=f"Date expression '{raw}' is ambiguous or in the past; please specify a clear date.",
        )

    now_local = clock.now()
    today_date = now_local.date()
    tz = clock.timezone

    # 1) Extract optional time-of-day
    parsed_time: time | None = None
    is_date_only = True

    m_ampm = _TIME_RE.search(low)
    m_24 = _TIME_24_RE.search(low)
    if m_ampm:
        hour = int(m_ampm.group(1))
        minute = int(m_ampm.group(2) or 0)
        ampm = m_ampm.group(3).lower()
        if hour < 1 or hour > 12 or minute > 59:
            return ParsedDateResult(
                raw_expression=raw,
                ambiguous=True,
                error=f"Invalid time in '{raw}'.",
            )
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        parsed_time = time(hour, minute)
        is_date_only = False
    elif m_24:
        hour = int(m_24.group(1))
        minute = int(m_24.group(2))
        parsed_time = time(hour, minute)
        is_date_only = False
    elif "morning" in low:
        parsed_time = time(9, 0)
        is_date_only = False
    elif "noon" in low:
        parsed_time = time(12, 0)
        is_date_only = False
    elif "afternoon" in low:
        parsed_time = time(14, 0)
        is_date_only = False
    elif "evening" in low:
        parsed_time = time(18, 0)
        is_date_only = False
    elif "tonight" in low or "night" in low:
        parsed_time = time(20, 0)
        is_date_only = False

    # 2) Extract date component
    target_date = None
    m_iso = _ISO_DATE_RE.search(low)
    m_in_days = _IN_DAYS_RE.search(low)

    if m_iso:
        try:
            target_date = datetime.strptime(m_iso.group(1), "%Y-%m-%d").date()
        except ValueError:
            return ParsedDateResult(
                raw_expression=raw, ambiguous=True, error=f"Invalid calendar date in '{raw}'."
            )
    elif "day after tomorrow" in low:
        target_date = today_date + timedelta(days=2)
    elif "tomorrow" in low:
        target_date = today_date + timedelta(days=1)
    elif "today" in low or "tonight" in low:
        target_date = today_date
    elif "end of this week" in low or "end of the week" in low:
        days_to_sun = (6 - today_date.weekday()) % 7
        target_date = today_date + timedelta(days=days_to_sun)
    elif m_in_days:
        tok = m_in_days.group(1).lower()
        n_days = int(tok) if tok.isdigit() else _NUM_WORDS.get(tok, 0)
        if n_days <= 0:
            return ParsedDateResult(
                raw_expression=raw, ambiguous=True, error=f"Invalid day offset in '{raw}'."
            )
        target_date = today_date + timedelta(days=n_days)
    else:
        # Check weekday: "this <day>", "next <day>", or bare "<day>"
        matched_wd = None
        for name, wd in _WEEKDAYS.items():
            if re.search(rf"\b{name}\b", low):
                matched_wd = wd
                break
        if matched_wd is not None:
            cur_wd = today_date.weekday()
            if "next " in low:
                # Next week's weekday (at least 7 - cur_wd + matched_wd if matched_wd <= cur_wd,
                # or next week's occurrence: always in the next calendar week Mon..Sun)
                days_to_next_mon = 7 - cur_wd
                target_date = today_date + timedelta(days=days_to_next_mon + matched_wd)
            elif "this " in low:
                delta = (matched_wd - cur_wd) % 7
                target_date = today_date + timedelta(days=delta)
            else:
                # Bare weekday -> upcoming occurrence (today if same day? usually upcoming)
                delta = (matched_wd - cur_wd) % 7
                if delta == 0:
                    delta = 7
                target_date = today_date + timedelta(days=delta)
        elif not is_date_only:
            # Time only (e.g. "at 5 PM") -> today
            target_date = today_date

    if target_date is None:
        return ParsedDateResult(
            raw_expression=raw,
            ambiguous=True,
            error=f"Could not resolve a clear date from '{raw}'.",
        )

    eff_time = parsed_time if parsed_time is not None else time(23, 59, 59)
    resolved = datetime.combine(target_date, eff_time, tzinfo=tz)
    return ParsedDateResult(
        raw_expression=raw,
        resolved_at=resolved,
        is_date_only=is_date_only,
        display_text=_format_display(resolved, is_date_only),
    )
