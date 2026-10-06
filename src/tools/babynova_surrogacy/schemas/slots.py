from typing import List, Optional

from pydantic import BaseModel, Field

from tools.babynova_surrogacy.core.config import BUSINESS_TZ


class SlotsRequest(BaseModel):
    iana_timezone: str = Field(
        default=BUSINESS_TZ,
        examples=["America/Bogota"],
        description="Zona horaria del usuario, ej. 'America/Bogota'.",
    )


class Slot(BaseModel):
    start_co: str
    end_co: str
    start_local: str
    end_local: str


class SlotsResponse(BaseModel):
    success: bool
    iana_timezone: str
    available_slots: List[Slot]
    errors: Optional[str] = None

