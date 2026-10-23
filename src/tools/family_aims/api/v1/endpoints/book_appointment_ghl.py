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
from tools.family_aims.services.ghl_calendar import (
    get_available_slots as ghl_get_available_slots,
    list_events as ghl_list_events,
    upsert_contact as ghl_upsert_contact,
    create_appointment as ghl_create_appointment,
    extract_appointment,
    get_meet_link,
)

router = APIRouter()

@router.post("/book-appointment-ghl", response_model=BookAppointmentResponse)
async def book_appointment_ghl(req: BookAppointmentRequest):
    settings = get_settings()
    gmail = settings.gmail

    calendar_type = req.calendar_type.upper()
    if calendar_type == "SUR":
        tool = settings.sur
        policy = booking_sur
        env_suffix = "SUR"
    else:  # Default to IVF
        tool = settings.ivf
        policy = booking_ivf
        env_suffix = "IVF"

    try:
        now = datetime.now(ZoneInfo("UTC"))

        try:
            start_dt = engine.parse_start_date(req.start_date)
        except ValueError:
            return BookAppointmentResponse(success=False, reason="invalid_start_date", errors="start_date is not a valid ISO 8601 date")

        # Duración tomada desde .env (no del request) como se solicitó
        duration_minutes = int(tool.duration_minutes)
        end_dt = engine.compute_end_date(start_dt, duration_minutes)

        if not engine.is_future(start_dt, now):
            return BookAppointmentResponse(success=False, reason="past_date", errors="The date cannot be in the past")

        weekday = engine.get_weekday_in_tz(start_dt, "America/Bogota") if hasattr(engine, "get_weekday_in_tz") else None
        hour = engine.get_hour_in_tz(start_dt, "America/Bogota") if hasattr(engine, "get_hour_in_tz") else None
        if weekday is None or hour is None:
            # Keep parity with Google endpoint utils
            from tools.family_aims.utils.timezones import get_hour_in_tz, get_weekday_in_tz
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
        client_tz = (req.iana_timezone or "").strip() or "America/Bogota"

        # Validación de configuración GHL
        if not settings.ghl_token:
            return BookAppointmentResponse(success=False, errors="Missing GHL_ACCESS_TOKEN/GHL_TOKEN in environment")
        if not settings.ghl_location_id:
            return BookAppointmentResponse(success=False, errors="Missing GHL_LOCATION_ID in environment")
        if not tool.ghl_calendar_id:
            return BookAppointmentResponse(success=False, errors=f"Missing FA_GHL_CALENDAR_ID_{env_suffix} in environment")

        # Validador de disponibilidad: comprobar solapamiento con eventos reales en GHL
        # Config note: en desarrollo IVF y SUR pueden compartir ID; no bloqueamos.
        # Si quieres forzar validación de IDs distintos, re-habilita el guard.

        # Overlap-based validator using real events
        try:
            existing = await ghl_list_events(
                access_token=settings.ghl_token,
                location_id=settings.ghl_location_id,
                calendar_id=tool.ghl_calendar_id,
                start_dt=start_dt,
                end_dt=end_dt,
            )
            events = existing.get("events", []) if isinstance(existing, dict) else []
            if events:
                # Consider overlap only if ev_start < end_dt and ev_end > start_dt (strict on end==start)
                from datetime import datetime as _dt
                def _p(v: str):
                    try:
                        return _dt.fromisoformat(v.replace("Z", "+00:00"))
                    except Exception:
                        return None
                conflict = False
                for ev in events:
                    ev_s_raw = ev.get("startTime") or ev.get("start_time") or ev.get("start")
                    ev_e_raw = ev.get("endTime") or ev.get("end_time") or ev.get("end")
                    ev_s = _p(ev_s_raw) if isinstance(ev_s_raw, str) else None
                    ev_e = _p(ev_e_raw) if isinstance(ev_e_raw, str) else None
                    if ev_s is None or ev_e is None:
                        # If we can't parse, be conservative: let GHL validate at creation
                        continue
                    # Normalize to aware datetimes; assume UTC if naive
                    if ev_s.tzinfo is None:
                        from zoneinfo import ZoneInfo as _ZI
                        ev_s = ev_s.replace(tzinfo=_ZI("UTC"))
                    if ev_e.tzinfo is None:
                        from zoneinfo import ZoneInfo as _ZI
                        ev_e = ev_e.replace(tzinfo=_ZI("UTC"))
                    if (ev_s < end_dt) and (ev_e > start_dt):
                        conflict = True
                        break
                if conflict:
                    return BookAppointmentResponse(success=False, reason="slot_taken", errors="That time is no longer available")
        except Exception:
            # No bloquea si la consulta de eventos falla; GHL volverá a validar al crear
            pass

        # Pre-chequeo B (no bloqueante): consultar eventos en GHL en el rango exacto.
        # Si hay resultados, igual dejamos que GHL valide en create_appointment,
        # porque free-slots es la fuente de verdad para disponibilidad pública.
        try:
            _ = await ghl_list_events(
                access_token=settings.ghl_token,
                location_id=settings.ghl_location_id,
                calendar_id=tool.ghl_calendar_id,
                start_dt=start_dt,
                end_dt=end_dt,
            )
        except Exception:
            pass

        # Resolver contact_id vía upsert (formato original)
        upsert = await ghl_upsert_contact(
            access_token=settings.ghl_token,
            location_id=settings.ghl_location_id,
            email=contact_email or None,
            phone=contact_phone or None,
            name=contact_name or None,
        )
        contact_obj = upsert.get("contact") or {}
        contact_id = contact_obj.get("id")
        if not contact_id:
            return BookAppointmentResponse(success=False, reason="creation_failed", errors="Failed to resolve/create the contact in GHL")

        # Crear la cita en GHL
        title = f"Family Aims ({policy.EVENT_SUMMARY_TAG}) and {contact_name}"
        try:
            created = await ghl_create_appointment(
                access_token=settings.ghl_token,
                calendar_id=tool.ghl_calendar_id,
                location_id=settings.ghl_location_id,
                contact_id=contact_id,
                start_dt=start_dt,
                end_dt=end_dt,
                title=title,
                appointment_status="confirmed",
                to_notify=False,
            )
        except Exception as exc:
            # Intentamos distinguir si es por conflicto/validación y exponer el mensaje real de GHL
            import httpx
            if isinstance(exc, httpx.HTTPStatusError) and exc.response is not None:
                status = exc.response.status_code
                err_text = None
                try:
                    payload = exc.response.json()
                    # GHL suele incluir message, details o errors
                    err_text = payload.get("message") or payload.get("error") or str(payload)
                except Exception:
                    err_text = exc.response.text

                if status == 400:
                    # Si viene texto, inclúyelo para diagnosticar (duración/reglas/owner-constraints)
                    msg = "That time is no longer available"
                    if err_text:
                        msg = f"{msg}. GHL: {err_text}"
                    return BookAppointmentResponse(success=False, reason="slot_taken", errors=msg)
                # Otros errores devuelven el status y mensaje bruto
                return BookAppointmentResponse(success=False, reason="creation_failed", errors=f"GHL error {status}: {err_text}")
            # Error no HTTP
            return BookAppointmentResponse(success=False, reason="creation_failed", errors=str(exc))

        appointment = extract_appointment(created)
        event_id = appointment.get("id")
        meet_link = get_meet_link(created)

        if not event_id:
            return BookAppointmentResponse(success=False, reason="creation_failed", errors="Failed to create the appointment. The booking was not completed")

        # Enviar correo igual que en Google Calendar
        try:
            if not (gmail.google_client_id and gmail.google_client_secret and gmail.google_refresh_token and gmail.sender_email):
                raise RuntimeError("Missing Gmail credentials (FA_GOOGLE_CLIENT_ID_GMAIL / FA_GMAIL_SENDER_EMAIL, etc.) in environment")

            from tools.family_aims.services.google_calendar import get_access_token as google_get_access_token
            gmail_token = await google_get_access_token(gmail.google_client_id, gmail.google_client_secret, gmail.google_refresh_token)
            local_start = start_dt.astimezone(ZoneInfo(client_tz))
            html_body = render_email_template(
                f"{policy.EMAIL_TEMPLATE_PREFIX}_{language.lower()}",
                contact_name=contact_name,
                agent_name=policy.AGENT_NAME,
                booking_date=date_formatting.format_full_date(local_start, language),
                booking_time=date_formatting.format_booking_time(local_start, language),
                meet_link=meet_link or "",
                event_link="",
            )
            await send_email(gmail_token, gmail.sender_email, contact_email, engine.EMAIL_SUBJECTS[language], html_body)
        except Exception as email_error:
            return BookAppointmentResponse(
                success=True, event_id=event_id, event_link=None, meet_link=meet_link,
                errors=f"The appointment was created but email notification failed: {email_error}",
            )

        return BookAppointmentResponse(success=True, event_id=event_id, event_link=None, meet_link=meet_link, errors=None)

    except Exception as e:
        return BookAppointmentResponse(success=False, errors=str(e))
