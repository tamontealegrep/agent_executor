from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from tools.family_aims.core.config import get_settings
from tools.family_aims.schemas.booking import (
    AppointmentInfo,
    FindAppointmentRequest,
    FindAppointmentResponse,
)
from tools.family_aims.services import date_formatting
from tools.family_aims.services.google_calendar import get_access_token, list_events
from tools.family_aims.utils.timezones import parse_iso

router = APIRouter()


@router.post("/find-appointment", response_model=FindAppointmentResponse)
async def find_appointment(req: FindAppointmentRequest):
    settings = get_settings()
    client_tz = req.iana_timezone.strip() or "America/Bogota"
    target_person = req.contact_name.strip()
    target_email = req.contact_email.strip().lower()
    language = req.language.upper()
    requested_cal = req.calendar_type.upper()

    if requested_cal not in ("IVF", "SUR"):
        return FindAppointmentResponse(success=False, errors="Debe proporcionar un calendar_type válido ('IVF' o 'SUR')")

    if not target_person and not target_email:
        return FindAppointmentResponse(success=False, errors="Debe proporcionar el nombre o el correo de la persona")

    try:
        # Rango: desde ayer hasta dentro de 35 días (para asegurar cobertura total)
        now = datetime.now(ZoneInfo("UTC"))
        time_min = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        time_max = time_min + timedelta(days=36)

        all_calendars = [
            ("IVF", settings.ivf),
            ("SUR", settings.sur),
        ]

        # Filtrar calendarios (ahora es obligatorio mandar IVF o SUR)
        calendars = [c for c in all_calendars if c[0] == requested_cal]

        found_appointments = []
        target_lower = target_person.lower()
        # Palabras clave del nombre (ignorando conectores comunes)
        target_keywords = [w for w in target_lower.split() if len(w) > 2 and w not in ("family", "aims", "and")]

        for cal_type, config in calendars:
            if not (config.google_client_id and config.google_client_secret and config.google_refresh_token):
                continue

            access_token = await get_access_token(
                config.google_client_id, config.google_client_secret, config.google_refresh_token
            )
            if not access_token:
                continue

            events = await list_events(
                access_token=access_token,
                calendar_id=config.calendar_id,
                time_min=time_min,
                time_max=time_max,
                # Quitamos el query 'q' para que Google no filtre por nosotros
                # y así asegurarnos de traer TODOS los eventos del mes para procesarlos aquí.
            )

            for event in events:
                summary = event.get("summary", "")
                description = event.get("description", "")
                attendees = event.get("attendees", [])
                
                if "Family Aims" not in summary:
                    continue

                content_to_search = (summary + " " + description).lower()
                
                # Búsqueda por nombre
                name_match = False
                if target_person:
                    name_match = (target_lower in content_to_search) or (
                        all(kw in content_to_search for kw in target_keywords) if target_keywords else False
                    )
                
                # Búsqueda por correo (en contenido o en lista de asistentes)
                email_match = False
                if target_email:
                    in_content = target_email in content_to_search
                    in_attendees = any(target_email == a.get("email", "").lower() for a in attendees)
                    email_match = in_content or in_attendees

                if name_match or email_match:
                    start_raw = event["start"].get("dateTime", event["start"].get("date"))
                    start_dt = parse_iso(start_raw).astimezone(ZoneInfo(client_tz))
                    
                    formatted_date = date_formatting.format_full_date(start_dt, language)
                    formatted_time = date_formatting.format_booking_time(start_dt, language)

                    found_appointments.append(
                        AppointmentInfo(
                            event_id=event["id"],
                            summary=summary,
                            start_time=f"{formatted_date} a las {formatted_time}",
                            calendar_type=cal_type,
                        )
                    )

        if found_appointments:
            return FindAppointmentResponse(success=True, appointments=found_appointments)

        target_search = target_person or target_email
        return FindAppointmentResponse(
            success=False, errors=f"No se encontró ninguna cita para {target_search} en el próximo mes."
        )

    except Exception as e:
        return FindAppointmentResponse(success=False, errors=str(e))
