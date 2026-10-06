from fastapi import APIRouter

from tools.babynova_surrogacy.core.config import get_settings
from tools.babynova_surrogacy.schemas.booking import (
    CancelAppointmentRequest,
    CancelAppointmentResponse,
)
from tools.babynova_surrogacy.services.google_calendar import delete_event, get_access_token

router = APIRouter()


@router.post("/cancel-appointment", response_model=CancelAppointmentResponse)
async def cancel_appointment(req: CancelAppointmentRequest):
    settings = get_settings()
    
    if not (settings.google_client_id and settings.google_client_secret and settings.google_refresh_token):
        return CancelAppointmentResponse(success=False, errors="Missing Google credentials in environment")

    try:
        access_token = await get_access_token(
            settings.google_client_id, settings.google_client_secret, settings.google_refresh_token
        )
        if not access_token:
            return CancelAppointmentResponse(success=False, errors="Failed to obtain Google Calendar access token")

        success = await delete_event(access_token, settings.calendar_id, req.event_id)
        
        if success:
            return CancelAppointmentResponse(success=True, message="The appointment has been successfully canceled.")
        else:
            return CancelAppointmentResponse(success=False, errors="Could not delete the event. Please verify the event_id.")

    except Exception as e:
        return CancelAppointmentResponse(success=False, errors=str(e))

