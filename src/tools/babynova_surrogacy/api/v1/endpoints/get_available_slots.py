from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter

from tools.babynova_surrogacy.core.config import BUSINESS_TZ, get_settings
from tools.babynova_surrogacy.schemas.slots import SlotsRequest, SlotsResponse
from tools.babynova_surrogacy.services.google_calendar import get_access_token, get_free_busy
from tools.babynova_surrogacy.services.slot_calculator import compute_available_slots
from tools.babynova_surrogacy.utils.timezones import add_days

router = APIRouter()


@router.post("/get-available-slots", response_model=SlotsResponse)
async def get_available_slots(req: SlotsRequest):
    settings = get_settings()
    user_tz = req.iana_timezone.strip() if req.iana_timezone and req.iana_timezone.strip() else BUSINESS_TZ

    try:
        now = datetime.now(ZoneInfo("UTC"))

        if not (settings.google_client_id and settings.google_client_secret and settings.google_refresh_token):
            return SlotsResponse(
                success=False, iana_timezone=user_tz, available_slots=[],
                errors="Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET / GOOGLE_REFRESH_TOKEN in environment",
            )
        access_token = await get_access_token(
            settings.google_client_id, settings.google_client_secret, settings.google_refresh_token
        )
        if not access_token:
            return SlotsResponse(success=False, iana_timezone=user_tz, available_slots=[], errors="Failed to obtain access token")

        range_end = add_days(now, settings.days_ahead)
        busy_events = await get_free_busy(access_token, settings.calendar_id, now, range_end)

        slots = compute_available_slots(
            now=now,
            days_ahead=settings.days_ahead,
            duration_minutes=settings.duration_minutes,
            gap_minutes=settings.gap_minutes,
            user_tz=user_tz,
            busy_events=busy_events,
        )

        return SlotsResponse(success=True, iana_timezone=user_tz, available_slots=slots, errors=None)

    except Exception as e:
        return SlotsResponse(success=False, iana_timezone=user_tz, available_slots=[], errors=str(e))

