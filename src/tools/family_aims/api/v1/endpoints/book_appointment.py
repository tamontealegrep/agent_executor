from datetime import datetime
from zoneinfo import ZoneInfo
from fastapi import APIRouter

from tools.family_aims.core.config import get_settings
from tools.family_aims.schemas.booking import BookAppointmentRequest, BookAppointmentResponse
from tools.family_aims.services import booking_ivf, booking_sur
from tools.family_aims.services import booking_engine as engine
from tools.family_aims.services import date_formatting
from tools.family_aims.services.email_templates import render_email_template
from tools.family_aims.services.gmail import send_email
from tools.family_aims.services.google_calendar import create_event, get_access_token, get_free_busy
from tools.family_aims.utils.timezones import get_hour_in_tz, get_weekday_in_tz

router = APIRouter()

@router.post("/book-appointment", response_model=BookAppointmentResponse)
async def book_appointment(req: BookAppointmentRequest):
    settings = get_settings()
    gmail = settings.gmail
    
    calendar_type = req.calendar_type.upper()
    if calendar_type == "SUR":
        tool = settings.sur
        policy = booking_sur
        env_suffix = "SUR"
    else: # Default to IVF
        tool = settings.ivf
        policy = booking_ivf
        env_suffix = "IVF"

    try:
        now = datetime.now(ZoneInfo("UTC"))

        try:
            start_dt = engine.parse_start_date(req.start_date)
        except ValueError:
            return BookAppointmentResponse(success=False, reason="invalid_start_date", errors="start_date is not a valid ISO 8601 date")

        duration_minutes = engine.parse_duration_minutes(req.duration)
        end_dt = engine.compute_end_date(start_dt, duration_minutes)

        if not engine.is_future(start_dt, now):
            return BookAppointmentResponse(success=False, reason="past_date", errors="The date cannot be in the past")

        weekday = get_weekday_in_tz(start_dt, "America/Bogota")
        hour = get_hour_in_tz(start_dt, "America/Bogota")
        minute = start_dt.astimezone(ZoneInfo("America/Bogota")).minute
        if not policy.is_valid_business_hour(weekday, hour, minute):
            return BookAppointmentResponse(success=False, reason="invalid_hour", errors="The requested time is outside business hours")

        if not engine.is_valid_email(req.contact_email):
            return BookAppointmentResponse(success=False, reason="invalid_email", errors="The contact email is not valid")

        contact_email = engine.normalize_email(req.contact_email)
        contact_name = engine.format_contact_name(req.contact_name)
        contact_phone = req.contact_phone.strip()
        language = engine.normalize_language(req.language)
        client_tz = req.iana_timezone.strip() or "America/Bogota"

        if not (tool.google_client_id and tool.google_client_secret and tool.google_refresh_token):
            return BookAppointmentResponse(success=False, errors=f"Missing GOOGLE_CLIENT_ID_{env_suffix} / GOOGLE_CLIENT_SECRET_{env_suffix} / GOOGLE_REFRESH_TOKEN_{env_suffix} in environment")

        access_token = await get_access_token(tool.google_client_id, tool.google_client_secret, tool.google_refresh_token)
        if not access_token:
            return BookAppointmentResponse(success=False, errors="Failed to obtain Google Calendar access token")

        busy_events = await get_free_busy(access_token, tool.calendar_id, start_dt, end_dt)
        if busy_events:
            return BookAppointmentResponse(success=False, reason="slot_taken", errors="That time is no longer available")

        event = await create_event(
            access_token=access_token,
            calendar_id=tool.calendar_id,
            summary=f"Family Aims ({policy.EVENT_SUMMARY_TAG}) and {contact_name}",
            description=engine.format_event_description(contact_name, contact_email, contact_phone),
            start_dt=start_dt,
            end_dt=end_dt,
            attendee_email=contact_email,
            request_id=f"meet-{start_dt.isoformat()}",
        )
        event_id = event.get("id")
        if not event_id:
            return BookAppointmentResponse(success=False, reason="creation_failed", errors="Failed to create the appointment. The booking was not completed")

        event_link = event.get("htmlLink")
        meet_link = event.get("hangoutLink")

        try:
            if not (gmail.google_client_id and gmail.google_client_secret and gmail.google_refresh_token and gmail.sender_email):
                raise RuntimeError("Missing Gmail credentials (FA_GOOGLE_CLIENT_ID_GMAIL / FA_GMAIL_SENDER_EMAIL, etc.) in environment")

            gmail_token = await get_access_token(gmail.google_client_id, gmail.google_client_secret, gmail.google_refresh_token)
            local_start = start_dt.astimezone(ZoneInfo(client_tz))
            html_body = render_email_template(
                f"{policy.EMAIL_TEMPLATE_PREFIX}_{language.lower()}",
                contact_name=contact_name,
                agent_name=policy.AGENT_NAME,
                booking_date=date_formatting.format_full_date(local_start, language),
                booking_time=date_formatting.format_booking_time(local_start, language),
                meet_link=meet_link or "",
                event_link=event_link or "",
            )
            await send_email(gmail_token, gmail.sender_email, contact_email, engine.EMAIL_SUBJECTS[language], html_body)
        except Exception as email_error:
            return BookAppointmentResponse(
                success=True, event_id=event_id, event_link=event_link, meet_link=meet_link,
                errors=f"The appointment was created but email notification failed: {email_error}",
            )

        return BookAppointmentResponse(success=True, event_id=event_id, event_link=event_link, meet_link=meet_link, errors=None)

    except Exception as e:
        return BookAppointmentResponse(success=False, errors=str(e))
