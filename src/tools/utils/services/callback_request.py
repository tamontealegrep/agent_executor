"""
Reglas de negocio puras para validar y normalizar una solicitud de callback
(llamada de vuelta). No sabe nada de HTTP, no envía correos ni crea eventos
de calendario — eso queda a cargo de una acción del flujo GHL que consuma
esta salida ya normalizada, ya que el mecanismo de notificación es
específico de cada cliente (credencial de Gmail, contact center, etc.) y
esta herramienta vive en tools.utils, sin lógica ni credencial de un
cliente en particular.
"""

from typing import Any, Dict, List, Optional, Tuple, Union
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_VALID_REASONS = {"no_availability", "booking_failed", "user_requested", "ambiguous_data", "technical_error"}

_DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
_WEEKDAYS = set(_DAY_NAMES[:5])
_WEEKEND = set(_DAY_NAMES[5:])

_VALID_TIME_WINDOWS = {"morning", "midday", "afternoon", "evening"}


def _is_present(val: Any) -> bool:
    return val is not None and str(val).strip() != ""


def _split_list(raw: Optional[Union[str, List[str]]]) -> List[str]:
    """Normaliza el input (string separado por comas, o lista) a una lista de strings sin vacios, en minusculas."""
    if raw is None:
        return []
    items = raw if isinstance(raw, list) else str(raw).split(",")
    return [str(item).strip().lower() for item in items if str(item).strip()]


def _normalize_preferred_days(raw: Optional[Union[str, List[str]]]) -> Tuple[List[str], Optional[str]]:
    """Acepta días individuales, y los atajos 'weekdays'/'weekend'/'any'. Devuelve (dias_ordenados, error)."""
    tokens = _split_list(raw)
    if not tokens:
        return [], None

    days = set()
    for token in tokens:
        if token == "any":
            return [], None
        elif token == "weekdays":
            days |= _WEEKDAYS
        elif token == "weekend":
            days |= _WEEKEND
        elif token in _DAY_NAMES:
            days.add(token)
        else:
            return [], f"Dia invalido en preferred_days: '{token}'"

    return [d for d in _DAY_NAMES if d in days], None


def _normalize_time_window(raw: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not _is_present(raw):
        return None, None
    token = str(raw).strip().lower()
    if token == "any":
        return None, None
    if token in _VALID_TIME_WINDOWS:
        return token, None
    return None, f"Franja horaria invalida en preferred_time_window: '{token}'"


def _is_valid_timezone(tz: str) -> bool:
    try:
        ZoneInfo(tz)
        return True
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        return False


def _rejected(error_message: str) -> Dict[str, Any]:
    return {
        "contact_name": None,
        "contact_phone": None,
        "contact_email": None,
        "reason": None,
        "context": None,
        "preferred_days": None,
        "preferred_time_window": None,
        "iana_timezone": None,
        "errors": error_message,
    }


def prepare_callback_request(
    raw_contact_name: Optional[str],
    raw_contact_phone: Optional[str],
    raw_contact_email: Optional[str],
    raw_reason: Optional[str],
    raw_context: Optional[str],
    raw_preferred_days: Optional[Union[str, List[str]]],
    raw_preferred_time_window: Optional[str],
    raw_timezone: Optional[str],
) -> Dict[str, Any]:
    """Valida y normaliza una solicitud de callback. No tiene efectos externos.
    Un input invalido/incompleto es un resultado de negocio normal (errors
    explica por que), no una excepcion — misma convencion que calculate_bmi
    y check_documentation."""
    contact_name = raw_contact_name.strip() if _is_present(raw_contact_name) else None
    contact_phone = raw_contact_phone.strip() if _is_present(raw_contact_phone) else None
    contact_email = raw_contact_email.strip() if _is_present(raw_contact_email) else None

    if not contact_name:
        return _rejected("contact_name es requerido")

    if not contact_phone and not contact_email:
        return _rejected("Debe proporcionar al menos contact_phone o contact_email")

    reason = raw_reason.strip().lower() if _is_present(raw_reason) else None
    if reason not in _VALID_REASONS:
        return _rejected(f"reason invalido. Valores permitidos: {', '.join(sorted(_VALID_REASONS))}")

    if not _is_present(raw_timezone):
        return _rejected("timezone es requerido")
    timezone = raw_timezone.strip()
    if not _is_valid_timezone(timezone):
        return _rejected(f"timezone invalido: '{timezone}'")

    preferred_days, days_error = _normalize_preferred_days(raw_preferred_days)
    if days_error:
        return _rejected(days_error)

    preferred_time_window, window_error = _normalize_time_window(raw_preferred_time_window)
    if window_error:
        return _rejected(window_error)

    context = raw_context.strip() if _is_present(raw_context) else None

    return {
        "contact_name": contact_name,
        "contact_phone": contact_phone,
        "contact_email": contact_email,
        "reason": reason,
        "context": context,
        "preferred_days": preferred_days,
        "preferred_time_window": preferred_time_window,
        "iana_timezone": timezone,
        "errors": None,
    }
