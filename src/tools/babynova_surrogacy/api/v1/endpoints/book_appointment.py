from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from tools.babynova_surrogacy.core.config import get_settings
from tools.babynova_surrogacy.schemas.booking import BookAppointmentRequest, BookAppointmentResponse
from tools.babynova_surrogacy.services import booking as policy
from tools.babynova_surrogacy.services.email_templates import render_email_template
from tools.babynova_surrogacy.services.gmail import send_email
from tools.babynova_surrogacy.services.google_calendar import create_event, get_access_token
from tools.babynova_surrogacy.utils.timezones import get_weekday_in_tz

router = APIRouter()


@router.post("/book-appointment", response_model=BookAppointmentResponse)
async def book_appointment(req: BookAppointmentRequest):
    settings = get_settings()
    gmail = settings.gmail

    try:
        now = datetime.now(ZoneInfo("UTC"))

        try:
            start_dt = policy.parse_start_date(req.start_date)
        except ValueError:
            return BookAppointmentResponse(success=False, reason="invalid_start_date", errors="start_date is not a valid ISO 8601 date")

        duration_minutes = policy.parse_duration_minutes(req.duration)
        end_dt = policy.compute_end_date(start_dt, duration_minutes)

        if not policy.is_future(start_dt, now):
            return BookAppointmentResponse(success=False, reason="past_date", errors="The date cannot be in the past")

        local_start = start_dt.astimezone(ZoneInfo("America/Bogota"))
        weekday = get_weekday_in_tz(start_dt, "America/Bogota")
        if not policy.is_valid_business_hour(weekday, local_start.hour, local_start.minute):
            return BookAppointmentResponse(success=False, reason="invalid_hour", errors="The requested time is outside business hours")

        contact_name = policy.format_contact_name(req.contact_name)
        contact_phone = (req.contact_phone or "").strip()
        contact_email = (req.contact_email or "").strip().lower()
        user_tz = (req.iana_timezone or "America/Bogota").strip()

        if not (settings.google_client_id and settings.google_client_secret and settings.google_refresh_token):
            return BookAppointmentResponse(success=False, errors="Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN in environment")
        if not settings.contact_center_email:
            return BookAppointmentResponse(success=False, errors="Missing NFS_CONTACT_CENTER_EMAIL in environment")

        access_token = await get_access_token(settings.google_client_id, settings.google_client_secret, settings.google_refresh_token)
        if not access_token:
            return BookAppointmentResponse(success=False, errors="Failed to obtain Google Calendar access token")

        event = await create_event(
            access_token=access_token,
            calendar_id=settings.calendar_id,
            summary=f"{policy.EVENT_TITLE_PREFIX}{policy.TITLE_SEPARATOR}{contact_name}{policy.TITLE_SEPARATOR}{contact_phone}",
            description=f"Email: {contact_email}\nPhone: {contact_phone}",
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
                raise RuntimeError("Missing Gmail credentials (NFS_GOOGLE_CLIENT_ID_GMAIL / NFS_GMAIL_SENDER_EMAIL, etc.) in environment")

            gmail_token = await get_access_token(gmail.google_client_id, gmail.google_client_secret, gmail.google_refresh_token)
            local_start = start_dt.astimezone(ZoneInfo(user_tz))
            html_body = render_email_template(
                policy.EMAIL_TEMPLATE_NAME,
                contact_name=contact_name,
                booking_date=local_start.strftime("%Y/%m/%d %H:%M"),
                contact_phone=contact_phone,
            )
            subject = f"{policy.EMAIL_SUBJECT_PREFIX}{policy.TITLE_SEPARATOR}{contact_name}{policy.TITLE_SEPARATOR}{contact_phone}"
            await send_email(gmail_token, gmail.sender_email, contact_email, subject, html_body)
        except Exception as email_error:
            # The appointment is already created (Google Calendar already notified attendee via sendUpdates=all)
            # Our own email is a best-effort add-on; do not fail the booking because of it.
            return BookAppointmentResponse(
                success=True, event_id=event_id, event_link=event_link, meet_link=meet_link,
                errors=f"The appointment was created but email notification to the contact center failed: {email_error}",
            )

        return BookAppointmentResponse(success=True, event_id=event_id, event_link=event_link, meet_link=meet_link, errors=None)

    except Exception as e:
        return BookAppointmentResponse(success=False, errors=str(e))

