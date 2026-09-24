from fastapi import APIRouter

from tools.family_aims.core.config import get_settings
from tools.family_aims.schemas.booking import (
    CancelAppointmentRequest,
    CancelAppointmentResponse,
)
from tools.family_aims.services.google_calendar import delete_event, get_access_token

router = APIRouter()


@router.post("/cancel-appointment", response_model=CancelAppointmentResponse)
async def cancel_appointment(req: CancelAppointmentRequest):
    settings = get_settings()
    
    config = settings.ivf if req.calendar_type == "IVF" else settings.sur
    
    if not (config.google_client_id and config.google_client_secret and config.google_refresh_token):
        return CancelAppointmentResponse(success=False, errors=f"Faltan credenciales para el calendario {req.calendar_type}")

    try:
        access_token = await get_access_token(
            config.google_client_id, config.google_client_secret, config.google_refresh_token
        )
        if not access_token:
            return CancelAppointmentResponse(success=False, errors="No se pudo obtener el access token de Google Calendar")

        success = await delete_event(access_token, config.calendar_id, req.event_id)
        
        if success:
            return CancelAppointmentResponse(success=True, message="La cita ha sido cancelada exitosamente.")
        else:
            return CancelAppointmentResponse(success=False, errors="No se pudo eliminar el evento. Verifique el event_id.")

    except Exception as e:
        return CancelAppointmentResponse(success=False, errors=str(e))
