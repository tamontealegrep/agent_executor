from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from tools.novafem_surrogacy.core.config import get_settings
from tools.novafem_surrogacy.schemas.booking import BookAppointmentRequest, BookAppointmentResponse
from tools.novafem_surrogacy.services import booking as policy
from tools.novafem_surrogacy.services.email_templates import render_email_template
from tools.novafem_surrogacy.services.gmail import send_email
from tools.novafem_surrogacy.services.google_calendar import create_event, get_access_token
from tools.novafem_surrogacy.utils.timezones import get_weekday_in_tz

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
            return BookAppointmentResponse(success=False, reason="invalid_start_date", errors="start_date no es una fecha ISO 8601 válida")

        duration_minutes = policy.parse_duration_minutes(req.duration)
        end_dt = policy.compute_end_date(start_dt, duration_minutes)

        if not policy.is_future(start_dt, now):
            return BookAppointmentResponse(success=False, reason="past_date", errors="La fecha no puede estar en el pasado")

        local_start = start_dt.astimezone(ZoneInfo("America/Bogota"))
        weekday = get_weekday_in_tz(start_dt, "America/Bogota")
        if not policy.is_valid_business_hour(weekday, local_start.hour, local_start.minute):
            return BookAppointmentResponse(success=False, reason="invalid_hour", errors="El horario solicitado está fuera del horario de atención")

        contact_name = policy.format_contact_name(req.contact_name)
        contact_phone = (req.contact_phone or "").strip()
        contact_email = (req.contact_email or "").strip().lower()
        user_tz = (req.iana_timezone or "America/Bogota").strip()

        if not (settings.google_client_id and settings.google_client_secret and settings.google_refresh_token):
            return BookAppointmentResponse(success=False, errors="Faltan GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN en el entorno")
        if not settings.contact_center_email:
            return BookAppointmentResponse(success=False, errors="Falta NFS_CONTACT_CENTER_EMAIL en el entorno")

        access_token = await get_access_token(settings.google_client_id, settings.google_client_secret, settings.google_refresh_token)
        if not access_token:
            return BookAppointmentResponse(success=False, errors="No se pudo obtener el access token de Google Calendar")

        event = await create_event(
            access_token=access_token,
            calendar_id=settings.calendar_id,
            summary=f"{policy.EVENT_TITLE_PREFIX} — {contact_name} — {contact_phone}",
            description=f"Email: {contact_email}\nPhone: {contact_phone}",
            start_dt=start_dt,
            end_dt=end_dt,
            attendee_email=contact_email,
            request_id=f"meet-{start_dt.isoformat()}",
        )
        event_id = event.get("id")
        if not event_id:
            return BookAppointmentResponse(success=False, reason="creation_failed", errors="No se pudo crear la cita. No se realizó la reserva")

        event_link = event.get("htmlLink")
        meet_link = event.get("hangoutLink")

        try:
            if not (gmail.google_client_id and gmail.google_client_secret and gmail.google_refresh_token and gmail.sender_email):
                raise RuntimeError("Faltan las credenciales de Gmail (NFS_GOOGLE_CLIENT_ID_GMAIL / NFS_GMAIL_SENDER_EMAIL, etc.) en el entorno")

            gmail_token = await get_access_token(gmail.google_client_id, gmail.google_client_secret, gmail.google_refresh_token)
            local_start = start_dt.astimezone(ZoneInfo(user_tz))
            html_body = render_email_template(
                policy.EMAIL_TEMPLATE_NAME,
                contact_name=contact_name,
                booking_date=local_start.strftime("%Y/%m/%d %H:%M"),
                contact_phone=contact_phone,
            )
            subject = f"{policy.EMAIL_SUBJECT_PREFIX} — {contact_name} — {contact_phone}"
            await send_email(gmail_token, gmail.sender_email, contact_email, subject, html_body)
        except Exception as email_error:
            # La cita ya quedo agendada (y Google Calendar ya notifico al asistente
            # via sendUpdates=all) — el correo propio es un plus, no lo tumbamos.
            return BookAppointmentResponse(
                success=True, event_id=event_id, event_link=event_link, meet_link=meet_link,
                errors=f"La cita se creó pero no se pudo notificar por correo al contact center: {email_error}",
            )

        return BookAppointmentResponse(success=True, event_id=event_id, event_link=event_link, meet_link=meet_link, errors=None)

    except Exception as e:
        return BookAppointmentResponse(success=False, errors=str(e))
