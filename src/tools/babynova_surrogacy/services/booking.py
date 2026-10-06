"""
Core booking helpers: parsing start/duration, contact-name formatting, and
Colombia business-hours validation. This is distinct from the availability
calculator (this one does attend Saturday, and Mon–Fri closes at 18:00).
Pure logic — no HTTP/Google concerns here.
"""

import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

_LEADING_INT_RE = re.compile(r"^\s*(-?\d+)")

# Public constants used by endpoints for consistent titles/subjects
EVENT_TITLE_PREFIX = "First Visit"
TITLE_SEPARATOR = " - "
EMAIL_SUBJECT_PREFIX = "First Visit"
EMAIL_TEMPLATE_NAME = "book_appointment"


def parse_start_date(start_date_str: str) -> datetime:
    """If start_date has no offset, assume America/Bogota (business TZ)."""
    dt = datetime.fromisoformat(start_date_str.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("America/Bogota"))
    return dt


def parse_duration_minutes(raw: Optional[str], default: int = 10) -> int:
    """JS-equivalent of parseInt(x, 10) || 10 (defaults to 10 minutes)."""
    if raw is None:
        return default
    match = _LEADING_INT_RE.match(raw)
    if not match:
        return default
    value = int(match.group(1))
    return value if value > 0 else default


def compute_end_date(start_dt: datetime, duration_minutes: int) -> datetime:
    return start_dt + timedelta(minutes=duration_minutes)


def is_future(start_dt: datetime, now_utc: datetime) -> bool:
    return start_dt > now_utc


def format_contact_name(raw_name: str) -> str:
    """Trim, collapse spaces, and capitalize each word."""
    return " ".join(word.capitalize() for word in raw_name.strip().split())


def is_valid_business_hour(weekday: int, hour: int, minute: int) -> bool:
    """Validate Colombia business hours.
    Weekday uses project convention (0=Sunday ... 6=Saturday, see utils/timezones.py).
    Sunday closed; Mon–Fri 07:00–18:00; Saturday 08:00–13:00.
    """
    if weekday == 0:  # Sunday
        return False
    if weekday in (1, 2, 3, 4, 5):  # Monday to Friday
        if hour < 7:
            return False
        if hour > 18 or (hour == 18 and minute > 0):
            return False
        return True
    if weekday == 6:  # Saturday
        if hour < 8:
            return False
        if hour > 13 or (hour == 13 and minute > 0):
            return False
        return True
    return False
