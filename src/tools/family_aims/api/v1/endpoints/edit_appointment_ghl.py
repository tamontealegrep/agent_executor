from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter

from tools.family_aims.core.config import get_settings
from tools.family_aims.schemas.booking import EditAppointmentRequest, EditAppointmentResponse
from tools.family_aims.services import booking_ivf, booking_sur
from tools.family_aims.services import booking_engine as engine
from tools.family_aims.services import date_formatting
from tools.family_aims.services.ghl_calendar import list_events as ghl_list_events, update_appointment as ghl_update_appointment
from tools.family_aims.utils.timezones import get_hour_in_tz, get_weekday_in_tz

router = APIRouter()

@router.post("/edit-appointment-ghl", response_model=EditAppointmentResponse)
async def edit_appointment_ghl(req: EditAppointmentRequest):
    settings = get_settings()

    calendar_type = req.calendar_type.upper()
    if calendar_type == "SUR":
        tool = settings.sur
        policy = booking_sur
    else:  # Default to IVF
        tool = settings.ivf
        policy = booking_ivf

    try:
        now = datetime.now(ZoneInfo("UTC"))

        if not settings.ghl_token:
            return EditAppointmentResponse(success=False, errors="Missing FA_GHL_TOKEN in environment")
        if not settings.ghl_location_id:
            return EditAppointmentResponse(success=False, errors="Missing FA_GHL_LOCATION_ID in environment")

        # 1) Procesar nuevos valores
        start_dt = None
        end_dt = None
        applied_duration = req.new_duration

        if req.new_start_date:
            try:
                start_dt = engine.parse_start_date(req.new_start_date)
            except ValueError:
                return EditAppointmentResponse(success=False, errors="new_start_date is not a valid ISO 8601 date")

            if not engine.is_future(start_dt, now):
                return EditAppointmentResponse(success=False, errors="The new date cannot be in the past")

            weekday = get_weekday_in_tz(start_dt, "America/Bogota")
            hour = get_hour_in_tz(start_dt, "America/Bogota")
            minute = start_dt.astimezone(ZoneInfo("America/Bogota")).minute
            if not policy.is_valid_business_hour(weekday, hour, minute):
                return EditAppointmentResponse(success=False, errors="The requested new time is outside business hours")

            # Duración: usar la enviada o la del .env si no viene
            duration_int = engine.parse_duration_minutes(applied_duration) if applied_duration else int(tool.duration_minutes)
            applied_duration = str(duration_int)
            end_dt = engine.compute_end_date(start_dt, duration_int)

            # Validar disponibilidad con GHL (excluyendo este evento)
            if tool.ghl_calendar_id:
                data = await ghl_list_events(
                    access_token=settings.ghl_token,
                    location_id=settings.ghl_location_id,
                    calendar_id=tool.ghl_calendar_id,
                    start_dt=start_dt,
                    end_dt=end_dt,
                )
                events = data.get("events", []) if isinstance(data, dict) else []
                others = [e for e in events if e.get("id") != req.event_id]
                if others:
                    return EditAppointmentResponse(success=False, errors="The requested new time is already taken by another appointment.")

        # 2) Actualizar en GHL
        updated = await ghl_update_appointment(
            access_token=settings.ghl_token,
            appointment_id=req.event_id,
            start_dt=start_dt,
            end_dt=end_dt,
        )
        if not isinstance(updated, dict):
            return EditAppointmentResponse(success=False, errors="Could not update the event. Please verify the event_id.")

        # 3) Formato de respuesta
        local_tz = req.iana_timezone or "America/Bogota"
        language = "EN"

        message = "The appointment has been successfully updated."
        new_time_str = None
        if start_dt:
            local_start = start_dt.astimezone(ZoneInfo(local_tz))
            formatted_date = date_formatting.format_full_date(local_start, language)
            formatted_time = date_formatting.format_booking_time(local_start, language)
            new_time_str = f"{formatted_date} at {formatted_time}"
            message = f"The appointment has been rescheduled to {new_time_str}."

        return EditAppointmentResponse(
            success=True,
            message=message,
            new_start_time=new_time_str,
            new_duration=applied_duration,
        )

    except Exception as e:
        return EditAppointmentResponse(success=False, errors=str(e))
