from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter

from tools.family_aims.core.config import get_settings
from tools.family_aims.schemas.slots import SlotsRequest, SlotsResponse, Slot
from tools.family_aims.services import slots_ivf, slots_sur
from tools.family_aims.services.slot_engine import compute_start_date, compute_end_date, is_holiday, BUSINESS_TZ
from tools.family_aims.services.ghl_calendar import get_available_slots as ghl_get_available_slots
from tools.family_aims.utils.timezones import format_iso_with_offset, get_weekday_in_tz

router = APIRouter()


def _meets_policy(start: datetime, end: datetime, policy) -> bool:
    """El slot libre de GHL debe caer en un día hábil, dentro de la ventana del día y fuera del almuerzo."""
    local_start = start.astimezone(ZoneInfo(BUSINESS_TZ))
    local_end = end.astimezone(ZoneInfo(BUSINESS_TZ))
    if is_holiday(local_start.year, local_start.month, local_start.day):
        return False
    window = policy.day_window(get_weekday_in_tz(start, BUSINESS_TZ))
    if not window:
        return False
    h1, m1, h2, m2 = window
    start_min = local_start.hour * 60 + local_start.minute
    end_min = local_end.hour * 60 + local_end.minute
    if start_min < h1 * 60 + m1 or end_min > h2 * 60 + m2:
        return False
    return not policy.lunch_break(start_min, end_min)


@router.post("/get-available-slots-ghl", response_model=SlotsResponse)
async def get_available_slots_ghl(req: SlotsRequest):
    settings = get_settings()
    user_tz = (req.iana_timezone or "").strip() or "America/Bogota"

    calendar_type = req.calendar_type.upper()
    if calendar_type == "SUR":
        tool = settings.sur
        policy = slots_sur
        env_suffix = "SUR"
    else:  # Default to IVF
        tool = settings.ivf
        policy = slots_ivf
        env_suffix = "IVF"

    try:
        now = datetime.now(ZoneInfo("UTC"))

        if not settings.ghl_token:
            return SlotsResponse(success=False, iana_timezone=user_tz, available_slots=[], errors="Missing GHL_ACCESS_TOKEN/GHL_TOKEN in environment")
        if not tool.ghl_calendar_id:
            return SlotsResponse(success=False, iana_timezone=user_tz, available_slots=[], errors=f"Missing FA_GHL_CALENDAR_ID_{env_suffix} in environment")

        start_date = compute_start_date(now)
        end_date = compute_end_date(start_date, tool.days_ahead, policy.closing_hour)

        # Consulta directa a GHL; timezone anclada al horario de negocio
        try:
            free_slots = await ghl_get_available_slots(
                access_token=settings.ghl_token,
                calendar_id=tool.ghl_calendar_id,
                start_dt=start_date,
                end_dt=end_date,
                timezone=BUSINESS_TZ,
            )
        except Exception as exc:
            return SlotsResponse(success=False, iana_timezone=user_tz, available_slots=[], errors=str(exc))

        duration = int(tool.duration_minutes)
        result = []
        for iso in free_slots:
            # ISO proviene en Z (UTC)
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            dt_end = dt + timedelta(minutes=duration)
            if not _meets_policy(dt, dt_end, policy):
                continue

            result.append(
                Slot(
                    start_co=format_iso_with_offset(dt, BUSINESS_TZ),
                    end_co=format_iso_with_offset(dt_end, BUSINESS_TZ),
                    start_local=format_iso_with_offset(dt, user_tz),
                    end_local=format_iso_with_offset(dt_end, user_tz),
                )
            )

        return SlotsResponse(success=True, iana_timezone=user_tz, available_slots=result, errors=None)

    except Exception as e:
        return SlotsResponse(success=False, iana_timezone=user_tz, available_slots=[], errors=str(e))
