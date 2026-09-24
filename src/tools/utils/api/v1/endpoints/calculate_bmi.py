from fastapi import APIRouter

from tools.utils.schemas.bmi import CalculateBmiRequest, CalculateBmiResponse
from tools.utils.services.bmi_calculator import calculate_bmi

router = APIRouter()


@router.post("/calculate-bmi", response_model=CalculateBmiResponse)
async def calculate_bmi_endpoint(req: CalculateBmiRequest):
    try:
        result = calculate_bmi(req.weight_kg, req.height_cm)
        return CalculateBmiResponse(success=True, **result)
    except Exception as e:
        return CalculateBmiResponse(success=False, errors=str(e))
