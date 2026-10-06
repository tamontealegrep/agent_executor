from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from tools.babynova_surrogacy.core.config import get_settings
from tools.babynova_surrogacy.schemas.booking import (
    AppointmentInfo,
    FindAppointmentRequest,
    FindAppointmentResponse,
)
from tools.babynova_surrogacy.services import date_formatting
from tools.babynova_surrogacy.services.google_calendar import get_access_token, list_events
from tools.babynova_surrogacy.utils.timezones import parse_iso

router = APIRouter()


@router.post("/find-appointment", response_model=FindAppointmentResponse)
async def find_appointment(req: FindAppointmentRequest):
    settings = get_settings()
    
    # NormalizaciÃ³n segura de inputs (manejo de None y strip)
    client_tz = (req.iana_timezone or "America/Bogota").strip()
    target_person = (req.contact_name or "").strip()
    target_phone = (req.contact_phone or "").strip()
    target_email = (req.contact_email or "").strip().lower()
    language = "ES"

    # 1. ValidaciÃ³n: Nombre, TelÃ©fono o Correo son obligatorios
    if not target_person and not target_phone and not target_email:
        return FindAppointmentResponse(success=False, errors="Debe proporcionar al menos el nombre, el telÃ©fono o el correo para realizar la bÃºsqueda.")

    # 2. NormalizaciÃ³n de TelÃ©fono (+57 si son 10 dÃ­gitos que empiezan con 3)
    clean_phone_digits = "".join(c for c in target_phone if c.isdigit())
    if len(clean_phone_digits) == 10 and clean_phone_digits.startswith("3"):
        target_phone = f"+57{clean_phone_digits}"
    
    # 3. ValidaciÃ³n bÃ¡sica de Correo (si se proporciona)
    if target_email and ("@" not in target_email or "." not in target_email.split("@")[1]):
        return FindAppointmentResponse(success=False, errors=f"El correo electrÃ³nico '{target_email}' no tiene un formato vÃ¡lido.")

    try:
        # Rango: desde ayer hasta dentro de 35 dÃ­as
        now = datetime.now(ZoneInfo("UTC"))
        time_min = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
        time_max = time_min + timedelta(days=36)

        if not (settings.google_client_id and settings.google_client_secret and settings.google_refresh_token):
            return FindAppointmentResponse(success=False, errors="Faltan credenciales de Google en el entorno")

        access_token = await get_access_token(
            settings.google_client_id, settings.google_client_secret, settings.google_refresh_token
        )
        if not access_token:
            return FindAppointmentResponse(success=False, errors="No se pudo obtener el access token de Google Calendar")

        events = await list_events(
            access_token=access_token,
            calendar_id=settings.calendar_id,
            time_min=time_min,
            time_max=time_max,
        )

        phone_matches = []
        name_matches = []
        email_matches = []

        target_lower = target_person.lower()
        target_keywords = [w for w in target_lower.split() if len(w) > 2 and w not in ("baby", "nova", "novafem", "primera", "vez")]
        clean_target_phone = "".join(c for c in target_phone if c.isdigit())

        for event in events:
            summary = event.get("summary", "")
            description = event.get("description", "")
            attendees = event.get("attendees", [])
            
            if "Primera Vez" not in summary:
                 continue

            # Extraer datos del tÃ­tulo: "Primera Vez â€” Nombre â€” TelÃ©fono"
            parts = [p.strip() for p in summary.split("â€”")]
            ext_name = parts[1] if len(parts) > 1 else ""
            ext_phone = parts[2] if len(parts) > 2 else ""
            
            # Extraer correo de descripciÃ³n o asistentes
            ext_email = None
            if "Email:" in description:
                try:
                    ext_email = description.split("Email:")[1].split("\n")[0].strip()
                except IndexError:
                    pass
            if not ext_email and attendees:
                ext_email = attendees[0].get("email")

            start_raw = event["start"].get("dateTime", event["start"].get("date"))
            start_dt = parse_iso(start_raw).astimezone(ZoneInfo(client_tz))
            formatted_date = date_formatting.format_full_date(start_dt, language)
            formatted_time = date_formatting.format_booking_time(start_dt, language)

            appt_info = AppointmentInfo(
                event_id=event["id"],
                summary=summary,
                start_time=f"{formatted_date} a las {formatted_time}",
                contact_name=ext_name or None,
                contact_phone=ext_phone or None,
                contact_email=ext_email or None
            )

            # --- PriorizaciÃ³n de BÃºsqueda ---
            
            # 1. Prioridad: TelÃ©fono
            if clean_target_phone and ext_phone:
                clean_ext_phone = "".join(c for c in ext_phone if c.isdigit())
                if clean_target_phone in clean_ext_phone:
                    phone_matches.append(appt_info)
                    continue # Si matchea por telÃ©fono, es prioridad alta

            # 2. Correo (si se proporcionÃ³ y no hubo match de telÃ©fono aÃºn)
            if target_email and ext_email and target_email == ext_email.lower():
                email_matches.append(appt_info)
                continue

            # 3. Nombre
            if target_person and ext_name:
                content_to_search = (summary + " " + description).lower()
                if (target_lower in content_to_search) or (all(kw in content_to_search for kw in target_keywords) if target_keywords else False):
                    name_matches.append(appt_info)

        # Retornar resultados segÃºn prioridad
        results = phone_matches or email_matches or name_matches

        if results:
            return FindAppointmentResponse(success=True, appointments=results)

        target_search = target_phone or target_person or target_email
        return FindAppointmentResponse(
            success=False, errors=f"No se encontrÃ³ ninguna cita para '{target_search}' en el prÃ³ximo mes."
        )

    except Exception as e:
        return FindAppointmentResponse(success=False, errors=str(e))

