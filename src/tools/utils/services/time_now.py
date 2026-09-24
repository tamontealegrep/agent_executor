"""Regla de negocio pura: hora actual en una zona horaria dada. No sabe nada de HTTP."""

from datetime import datetime
from zoneinfo import ZoneInfo


def get_current_time(tz_name: str) -> str:
    """Hora actual en tz_name, en formato ISO 8601 con offset."""
    return datetime.now(ZoneInfo(tz_name)).isoformat()
