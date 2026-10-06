"""
Reglas de negocio puras: ventanas horarias, festivos, anticipaciÃ³n mÃ­nima
y el cÃ¡lculo final de slots. No sabe nada de HTTP ni de Google.
"""

from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from tools.babynova_surrogacy.core.config import BUSINESS_TZ, load_slots_config
from tools.babynova_surrogacy.schemas.slots import Slot
from tools.babynova_surrogacy.utils.timezones import (
    add_days,
    build_business_instant,
    format_iso_with_offset,
    get_date_key_in_tz,
    get_weekday_in_tz,
)


def get_day_window_hours(weekday: int, day: int) -> Optional[Tuple[int, int, int, int]]:
    """Ventana de atenciÃ³n (hora_ini, min_ini, hora_fin, min_fin) para ese dÃ­a de la semana, o None si no hay atenciÃ³n."""
    config = load_slots_config().get("windows", {})
    entry = config.get(weekday)
    if not entry:
        return None

    # Soporte para configuraciÃ³n flexible (especialmente sÃ¡bados)
    if isinstance(entry, dict):
        weeks = entry.get("weeks", [])
        window = entry.get("window")
        if not window:
            return None
        
        # Calcula si es la 1Âª, 2Âª, 3Âª, 4Âª o 5Âª semana (ocurrencia del dÃ­a en el mes)
        nth_week = (day - 1) // 7 + 1
        if nth_week not in weeks:
            return None
        return tuple(window)

    return tuple(entry)


def is_lunch_break(slot_start: datetime) -> bool:
    """Indica si el slot inicia dentro de la franja de almuerzo configurada."""
    config = load_slots_config().get("lunch_break", {})
    if not config:
        return False
    
    start_h = config.get("start_hour", 12)
    end_h = config.get("end_hour", 13)
    
    # Comparamos en minutos desde las 00:00 para flexibilidad
    slot_min = slot_start.hour * 60 + slot_start.minute
    return start_h * 60 <= slot_min < end_h * 60


def is_holiday(year: int, month: int, day: int) -> bool:
    """Indica si la fecha dada es festivo en Colombia."""
    holidays = load_slots_config().get("holidays", [])
    return f"{year:04d}-{month:02d}-{day:02d}" in holidays


def compute_processing_day_key(now_utc: datetime) -> str:
    """Clave 'YYYY-MM-DD' del dÃ­a hÃ¡bil siguiente a now_utc (salta fin de semana a lunes); referencia para la anticipaciÃ³n mÃ­nima."""
    d = add_days(now_utc, 1)
    dow = get_weekday_in_tz(d, BUSINESS_TZ)
    if dow == 0:  # domingo -> lunes
        d = add_days(d, 1)
    elif dow == 6:  # sÃ¡bado -> lunes
        d = add_days(d, 2)
    return get_date_key_in_tz(d, BUSINESS_TZ)


def valid_advance(slot_start_utc: datetime, processing_day_key: str) -> bool:
    """Indica si el slot cumple la anticipaciÃ³n mÃ­nima: su dÃ­a debe ser posterior al dÃ­a de procesamiento."""
    slot_day_key = get_date_key_in_tz(slot_start_utc, BUSINESS_TZ)
    return slot_day_key > processing_day_key


def get_candidate_days_list(start: datetime, end: datetime) -> List[dict]:
    """Lista los dÃ­as entre start y end que tienen ventana de atenciÃ³n y no son festivo."""
    days = []
    start_key = get_date_key_in_tz(start, BUSINESS_TZ)
    end_key = get_date_key_in_tz(end, BUSINESS_TZ)
    sy, sm, sd = (int(x) for x in start_key.split("-"))
    ey, em, ed = (int(x) for x in end_key.split("-"))

    cursor = build_business_instant(sy, sm, sd, 12, 0, BUSINESS_TZ)
    end_cursor = build_business_instant(ey, em, ed, 12, 0, BUSINESS_TZ)

    while cursor <= end_cursor:
        key = get_date_key_in_tz(cursor, BUSINESS_TZ)
        y, m, d = (int(x) for x in key.split("-"))
        weekday = get_weekday_in_tz(cursor, BUSINESS_TZ)
        window = get_day_window_hours(weekday, d)
        if window and not is_holiday(y, m, d):
            days.append({"year": y, "month": m, "day": d, "window": window})
        cursor = add_days(cursor, 1)

    return days


def compute_available_slots(
    now: datetime,
    days_ahead: int,
    duration_minutes: int,
    gap_minutes: int,
    user_tz: str,
    busy_events: List[Tuple[datetime, datetime]],
) -> List[Slot]:
    """Genera los slots reservables dentro de las ventanas de atenciÃ³n, excluyendo festivos, eventos ocupados y los que no cumplen la anticipaciÃ³n mÃ­nima."""
    range_end = add_days(now, days_ahead)
    processing_day_key = compute_processing_day_key(now)
    candidate_days = get_candidate_days_list(now, range_end)

    result_slots: List[Slot] = []
    step = duration_minutes + gap_minutes

    for c in candidate_days:
        sh, sm_, eh, em_ = c["window"]
        day_open = build_business_instant(c["year"], c["month"], c["day"], sh, sm_, BUSINESS_TZ)
        day_close = build_business_instant(c["year"], c["month"], c["day"], eh, em_, BUSINESS_TZ)

        cursor = day_open
        while cursor + timedelta(minutes=duration_minutes) <= day_close:
            slot_start = cursor
            slot_end = slot_start + timedelta(minutes=duration_minutes)

            is_opening_slot = slot_start == day_open
            check_start = slot_start if is_opening_slot else slot_start - timedelta(minutes=gap_minutes)
            check_end = slot_end

            overlaps = any(check_start < ev_end and check_end > ev_start for ev_start, ev_end in busy_events)
            lunch = is_lunch_break(slot_start)

            if not overlaps and not lunch and valid_advance(slot_start, processing_day_key):
                result_slots.append(Slot(
                    start_co=format_iso_with_offset(slot_start, BUSINESS_TZ),
                    end_co=format_iso_with_offset(slot_end, BUSINESS_TZ),
                    start_local=format_iso_with_offset(slot_start, user_tz),
                    end_local=format_iso_with_offset(slot_end, user_tz),
                ))

            cursor = cursor + timedelta(minutes=step)

    return result_slots

