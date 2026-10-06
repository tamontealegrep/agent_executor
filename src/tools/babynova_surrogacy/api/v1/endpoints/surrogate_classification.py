from fastapi import APIRouter

from tools.babynova_surrogacy.schemas.surrogate_classification import (
    SurrogateClassificationRequest,
    SurrogateClassificationResponse,
)
from tools.babynova_surrogacy.services.surrogate_classifier import classify_surrogate

router = APIRouter()


@router.post("/surrogate-classification", response_model=SurrogateClassificationResponse)
async def surrogate_classification(req: SurrogateClassificationRequest):
    try:
        result = classify_surrogate(req.model_dump())
        return SurrogateClassificationResponse(success=True, result=result, errors=None)
    except Exception as e:
        return SurrogateClassificationResponse(success=False, result=None, errors=str(e))

