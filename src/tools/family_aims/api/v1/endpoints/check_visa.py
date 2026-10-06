from fastapi import APIRouter, HTTPException
from tools.family_aims.schemas.visa import VisaCheckRequest, VisaCheckResponse
from tools.family_aims.services.visa_checker import VisaCheckerService

router = APIRouter()
visa_service = VisaCheckerService()

@router.post("/check-visa", response_model=VisaCheckResponse)
async def check_visa_requirement(request: VisaCheckRequest):
    """
    Revisa los requisitos de visa para una o varias nacionalidades para ingresar a Colombia.
    """
    result = visa_service.check_visas(request.nationalities)
    if not result.success:
        raise HTTPException(status_code=500, detail=result.errors)
    return result
