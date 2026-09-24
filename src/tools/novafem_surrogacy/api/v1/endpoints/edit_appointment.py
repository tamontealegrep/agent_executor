from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter

from tools.novafem_surrogacy.core.config import get_settings
from tools.novafem_surrogacy.schemas.booking import EditAppointmentRequest, EditAppointmentResponse
from tools.novafem_surrogacy.services import booking as policy
from tools.novafem_surrogacy.services import date_formatting
from tools.novafem_surrogacy.services.google_calendar import update_event, get_access_token, list_events
from tools.novafem_surrogacy.utils.timezones import get_weekday_in_tz

router = APIRouter()

@router.post("/edit-appointment", response_model=EditAppointmentResponse)
async def edit_appointment(req: EditAppointmentRequest):
    settings = get_settings()
    
    try:
        now = datetime.now(ZoneInfo("UTC"))
        
        # 1. Credenciales
        if not (settings.google_client_id and settings.google_client_secret and settings.google_refresh_token):
            return EditAppointmentResponse(success=False, errors="Faltan credenciales de Google en el entorno")

        access_token = await get_access_token(settings.google_client_id, settings.google_client_secret, settings.google_refresh_token)
        if not access_token:
            return EditAppointmentResponse(success=False, errors="No se pudo obtener el access token de Google Calendar")

        # 2. Procesar cambios de fecha/hora si se proporcionan
        start_dt = None
        end_dt = None
        applied_duration = req.new_duration
        
        if req.new_start_date:
            try:
                start_dt = policy.parse_start_date(req.new_start_date)
            except ValueError:
                return EditAppointmentResponse(success=False, errors="new_start_date no es una fecha ISO 8601 válida")

            if not policy.is_future(start_dt, now):
                return EditAppointmentResponse(success=False, errors="La nueva fecha no puede estar en el pasado")

            # Validar horario laboral
            local_start = start_dt.astimezone(ZoneInfo("America/Bogota"))
            weekday = get_weekday_in_tz(start_dt, "America/Bogota")
            if not policy.is_valid_business_hour(weekday, local_start.hour, local_start.minute):
                return EditAppointmentResponse(success=False, errors="El nuevo horario solicitado está fuera del horario de atención")

            # Calcular fin
            applied_duration = req.new_duration or str(settings.duration_minutes)
            duration_int = policy.parse_duration_minutes(applied_duration)
            end_dt = policy.compute_end_date(start_dt, duration_int)

            # Validar disponibilidad: buscamos eventos en el rango que no sean el actual
            existing_events = await list_events(access_token, settings.calendar_id, start_dt, end_dt)
            
            # Filtramos el evento actual de los resultados. Si queda algo, el slot está ocupado.
            other_events = [e for e in existing_events if e.get("id") != req.event_id]
            
            if other_events:
                return EditAppointmentResponse(success=False, errors="El nuevo horario solicitado ya está ocupado por otra cita.")

        # 3. Actualizar evento
        updated_event = await update_event(
            access_token=access_token,
            calendar_id=settings.calendar_id,
            event_id=req.event_id,
            start_dt=start_dt,
            end_dt=end_dt
        )

        if not updated_event:
            return EditAppointmentResponse(success=False, errors="No se pudo actualizar el evento. Verifique el event_id.")

        # 4. Formatear respuesta
        local_tz = (req.iana_timezone or "America/Bogota").strip()
        language = "ES"
        
        message = "La cita ha sido modificada exitosamente."
        new_time_str = None
        
        if start_dt:
            local_start = start_dt.astimezone(ZoneInfo(local_tz))
            formatted_date = date_formatting.format_full_date(local_start, language)
            formatted_time = date_formatting.format_booking_time(local_start, language)
            new_time_str = f"{formatted_date} a las {formatted_time}"
            message = f"La cita ha sido reprogramada para el {new_time_str}."

        return EditAppointmentResponse(
            success=True,
            message=message,
            new_start_time=new_time_str,
            new_duration=applied_duration
        )

    except Exception as e:
        return EditAppointmentResponse(success=False, errors=str(e))
