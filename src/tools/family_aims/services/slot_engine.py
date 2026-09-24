"""
Motor de cálculo de slots compartido por available-slots-ivf y available-slots-sur.
Port de la lógica común a los dos nodos de código de GHL: "code_01" (rango de
fechas de búsqueda, con la regla de anticipación mínima) y el núcleo de
"code_02" (generación y formato de slots). Lo que cada tool aporta por su
cuenta (ventana horaria por día, franja de almuerzo, hora de cierre del
último día) se le pasa como funciones — ver services/slots_ivf.py y
services/slots_sur.py.

No sabe nada de HTTP ni de Google. La salida usa el mismo formato que
tools.novafem_surrogacy/services/slot_calculator.py (start_co/end_co en
hora Bogotá, start_local/end_local en la zona pedida, ISO 8601 con offset —
unificado a pedido del owner, 2026-08-27, en vez de las horas legibles en
español que tenía antes esta herramienta).
"""

from datetime import date, datetime, timedelta
from typing import Callable, List, Optional, Tuple

from tools.family_aims.core.config import load_slots_config
from tools.family_aims.schemas.slots import Slot
from tools.family_aims.utils.timezones import (
    build_business_instant,
    format_iso_with_offset,
    get_date_in_tz,
    get_hour_in_tz,
    get_weekday_in_tz,
)

BUSINESS_TZ = "America/Bogota"

DayWindowFn = Callable[[int], Optional[Tuple[int, int, int, int]]]
LunchBreakFn = Callable[[int, int], bool]
ClosingHourFn = Callable[[int], Tuple[int, int]]


def is_holiday(year: int, month: int, day: int) -> bool:
    holidays = load_slots_config().get("holidays", [])
    return f"{year:04d}-{month:02d}-{day:02d}" in holidays


def _weekday_of_date(d: date) -> int:
    instant = build_business_instant(d.year, d.month, d.day, 12, 0, BUSINESS_TZ)
    return get_weekday_in_tz(instant, BUSINESS_TZ)


def compute_start_date(now_utc: datetime) -> datetime:
    """Port de code_01: primer día de la ventana de búsqueda, siempre a las 07:00 Bogotá.

    Regla de anticipación: normalmente +2 días; viernes antes de las 17:00 -> +3
    (lunes); viernes desde las 17:00 -> +4 (martes); sábado -> +3 (martes);
    domingo -> +2 (martes).
    """
    weekday = get_weekday_in_tz(now_utc, BUSINESS_TZ)
    hour = get_hour_in_tz(now_utc, BUSINESS_TZ)

    if weekday == 5:  # Viernes
        offset = 3 if hour < 17 else 4
    elif weekday == 6:  # Sabado
        offset = 3
    elif weekday == 0:  # Domingo
        offset = 2
    else:
        offset = 2

    local_date = get_date_in_tz(now_utc, BUSINESS_TZ) + timedelta(days=offset)
    return build_business_instant(local_date.year, local_date.month, local_date.day, 7, 0, BUSINESS_TZ)


def compute_end_date(start_date: datetime, days_ahead: int, closing_hour_fn: ClosingHourFn) -> datetime:
    """Port de code_01: último día de la ventana, days_ahead días después de
    start_date, retrocedido a viernes si cae en fin de semana. La hora de
    cierre depende del día de la semana final (closing_hour_fn), distinta
    por tool. En el JS original days_ahead venía fijo en 14; aquí es
    configurable (DAYS_AHEAD en .env)."""
    to_date = start_date.date() + timedelta(days=days_ahead)
    weekday = _weekday_of_date(to_date)

    if weekday == 6:  # Sabado -> viernes
        to_date -= timedelta(days=1)
        weekday = 5
    elif weekday == 0:  # Domingo -> viernes
        to_date -= timedelta(days=2)
        weekday = 5

    hour, minute = closing_hour_fn(weekday)
    return build_business_instant(to_date.year, to_date.month, to_date.day, hour, minute, BUSINESS_TZ)


def _format_slot(slot_start: datetime, slot_end: datetime, user_tz: str) -> Slot:
    return Slot(
        start_co=format_iso_with_offset(slot_start, BUSINESS_TZ),
        end_co=format_iso_with_offset(slot_end, BUSINESS_TZ),
        start_local=format_iso_with_offset(slot_start, user_tz),
        end_local=format_iso_with_offset(slot_end, user_tz),
    )


def compute_available_slots(
    start_date: datetime,
    end_date: datetime,
    now_utc: datetime,
    duration_minutes: int,
    interval_minutes: int,
    min_advance_minutes: int,
    user_tz: str,
    busy_events: List[Tuple[datetime, datetime]],
    day_window_fn: DayWindowFn,
    lunch_break_fn: LunchBreakFn,
) -> List[Slot]:
    """Port del núcleo de code_02: genera los slots dentro de [start_date, end_date],
    excluyendo festivos, la franja de almuerzo, eventos ocupados y los que no
    cumplen la anticipación mínima (min_advance_minutes desde ahora)."""
    min_advance_instant = now_utc + timedelta(minutes=min_advance_minutes)

    candidate_days = []
    cursor_date = start_date.date()
    end_date_only = end_date.date()
    while cursor_date <= end_date_only:
        weekday = _weekday_of_date(cursor_date)
        window = day_window_fn(weekday)
        if window and not is_holiday(cursor_date.year, cursor_date.month, cursor_date.day):
            candidate_days.append((cursor_date, window))
        cursor_date += timedelta(days=1)

    result: List[Slot] = []
    for day, (h1, m1, h2, m2) in candidate_days:
        day_open = build_business_instant(day.year, day.month, day.day, h1, m1, BUSINESS_TZ)
        day_close = build_business_instant(day.year, day.month, day.day, h2, m2, BUSINESS_TZ)

        cursor = day_open
        while cursor + timedelta(minutes=duration_minutes) <= day_close:
            slot_start = cursor
            slot_end = slot_start + timedelta(minutes=duration_minutes)
            start_min = slot_start.hour * 60 + slot_start.minute
            end_min = slot_end.hour * 60 + slot_end.minute

            if not lunch_break_fn(start_min, end_min):
                overlaps = any(slot_start < ev_end and slot_end > ev_start for ev_start, ev_end in busy_events)
                if not overlaps and slot_start >= min_advance_instant:
                    result.append(_format_slot(slot_start, slot_end, user_tz))

            cursor = cursor + timedelta(minutes=interval_minutes)

    return result
