"""
Port de code_01 del flujo GHL "book_appointment": parseo de fecha/duración,
formateo del nombre de contacto, y el validador de horario laboral de
Colombia — distinto del de available-slots (este SÍ atiende sábado, y
Lunes-Viernes cierra a las 18:00, no a las 17:00). No sabe nada de HTTP ni
de Google.
"""

import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

_LEADING_INT_RE = re.compile(r"^\s*(-?\d+)")

EVENT_TITLE_PREFIX = "Primera Vez"
EMAIL_SUBJECT_PREFIX = "🗓️ Primera Vez"
EMAIL_TEMPLATE_NAME = "book_appointment"


def parse_start_date(start_date_str: str) -> datetime:
    """Si start_date no trae offset, se asume hora de Bogotá (la zona del negocio)."""
    dt = datetime.fromisoformat(start_date_str.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("America/Bogota"))
    return dt


def parse_duration_minutes(raw: Optional[str], default: int = 10) -> int:
    """Equivalente a `parseInt(x, 10) || 10` del JS original (10 min por
    defecto — este flujo usa un valor distinto al de los demás tools)."""
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
    """Recorta, colapsa espacios y capitaliza cada palabra."""
    return " ".join(word.capitalize() for word in raw_name.strip().split())


def is_valid_business_hour(weekday: int, hour: int, minute: int) -> bool:
    """Port de validateColombiaBusinessHours del JS original. weekday sigue
    la convención del proyecto (0=Domingo ... 6=Sábado, ver utils/timezones.py).
    Domingo cerrado; Lunes a Viernes 07:00-18:00; Sábado 08:00-13:00."""
    if weekday == 0:  # Domingo
        return False
    if weekday in (1, 2, 3, 4, 5):  # Lunes a Viernes
        if hour < 7:
            return False
        if hour > 18 or (hour == 18 and minute > 0):
            return False
        return True
    if weekday == 6:  # Sábado
        if hour < 8:
            return False
        if hour > 13 or (hour == 13 and minute > 0):
            return False
        return True
    return False
