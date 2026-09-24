from fastapi import APIRouter

from tools.utils.schemas.time_now import TimeNowRequest, TimeNowResponse
from tools.utils.services.time_now import get_current_time

router = APIRouter()


@router.post("/time-now", response_model=TimeNowResponse)
async def time_now(req: TimeNowRequest):
    tz = req.iana_timezone.strip() if req.iana_timezone and req.iana_timezone.strip() else "UTC"
    try:
        now = get_current_time(tz)
        return TimeNowResponse(success=True, iana_timezone=tz, now=now, errors=None)
    except Exception as e:
        return TimeNowResponse(success=False, iana_timezone=tz, now=None, errors=str(e))
