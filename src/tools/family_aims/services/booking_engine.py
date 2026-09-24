"""
Motor compartido por book-appointment-ivf y book-appointment-sur. Port de la
parte de "code_01" que es común a los dos nodos GHL: parseo/validación de
fecha, formateo del nombre de contacto, validación de email e idioma. Lo que
cada tool aporta por su cuenta (nombre del agente, calendario, validador de
horario laboral, templates de correo) vive en services/booking_ivf.py y
services/booking_sur.py.

No sabe nada de HTTP ni de Google.
"""

import re
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
_LEADING_INT_RE = re.compile(r"^\s*(-?\d+)")

EMAIL_SUBJECTS = {
    "ES": "🎉 ¡Cita confirmada! Detalles de tu consulta con Family Aims",
    "EN": "🎉 Appointment Confirmed! Details for your consultation with Family Aims",
    "PT": "🎉 Consulta confirmada! Detalhes da sua consulta com a Family Aims",
}

# Descripción del evento de Calendar: igual para ambos tools en el JSON
# original de GHL (bilingüe EN/ES fijo, sin importar el idioma solicitado
# para el correo — así estaba, no se cambia).
EVENT_DESCRIPTION_TEMPLATE = (
    "This consultation will allow us to better understand your situation, expectations, and "
    "family-building goals so we can identify the fertility treatment or program that best "
    "fits your needs. It is also an opportunity to answer your questions and provide "
    "personalized guidance throughout the process. Your attendance is essential for us to "
    "recommend the best path toward achieving your family-building goals.\n\n"
    "Esta cita nos permitira conocer tu caso, expectativas y objetivos para ayudarte a "
    "identificar el tratamiento o programa de fertilidad que mejor se adapte a tus "
    "necesidades. Tambien sera una oportunidad para resolver tus dudas y brindarte "
    "orientacion personalizada en cada paso del proceso. Tu asistencia es fundamental para "
    "ofrecerte la mejor alternativa para construir tu familia.\n\n"
    "Name: {name}\nEmail: {email}\nPhone: {phone}"
)


def format_event_description(name: str, email: str, phone: str) -> str:
    return EVENT_DESCRIPTION_TEMPLATE.format(name=name, email=email, phone=phone)


def parse_start_date(start_date_str: str) -> datetime:
    """Parsea start_date a un datetime aware. Si no trae offset, se asume
    hora de Bogotá (la zona del negocio) — pensado para encadenar con el
    start_co que ya devuelve available-slots-*."""
    dt = datetime.fromisoformat(start_date_str.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("America/Bogota"))
    return dt


def parse_duration_minutes(raw: Optional[str], default: int = 20) -> int:
    """Equivalente a `parseInt(x, 10) || 20` del JS original, con una guarda
    extra: una duración <= 0 tampoco es válida (el JS original sí la dejaba
    pasar, un descuido menor que no vale la pena replicar)."""
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


def normalize_email(raw_email: str) -> str:
    return raw_email.strip().lower()


def is_valid_email(raw_email: str) -> bool:
    email = normalize_email(raw_email)
    if not _EMAIL_RE.match(email):
        return False
    if "@example." in email:
        return False
    return True


def normalize_language(raw_language: str) -> str:
    """ES o PT si vienen explícitos; cualquier otra cosa cae a inglés (igual
    que el JS original, que solo distinguía ES vs "todo lo demás")."""
    lang = (raw_language or "").strip().upper()
    return lang if lang in ("ES", "PT") else "EN"
