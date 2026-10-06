from typing import List, Optional

from pydantic import BaseModel, Field


class BookAppointmentRequest(BaseModel):
    start_date: str = Field(examples=["2026-08-28T09:00:00-05:00"])
    duration: Optional[str] = Field(default="10", examples=["10"])
    contact_name: str = Field(examples=["Maria Perez"])
    contact_email: str = Field(examples=["maria.perez@example.com"])
    contact_phone: str = Field(examples=["+573001234567"])
    iana_timezone: Optional[str] = Field(default="America/Bogota", examples=["America/Bogota"])


class BookAppointmentResponse(BaseModel):
    success: bool
    reason: Optional[str] = None
    event_id: Optional[str] = None
    event_link: Optional[str] = None
    meet_link: Optional[str] = None
    errors: Optional[str] = None


class FindAppointmentRequest(BaseModel):
    contact_name: Optional[str] = Field(default="", examples=["John Doe"])
    contact_phone: Optional[str] = Field(default="", examples=["+573001234567"])
    contact_email: Optional[str] = Field(default="", examples=["john@example.com"])
    iana_timezone: Optional[str] = Field(default="America/Bogota", examples=["America/Bogota"])


class AppointmentInfo(BaseModel):
    event_id: str
    summary: str
    start_time: str
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None


class FindAppointmentResponse(BaseModel):
    success: bool
    appointments: List[AppointmentInfo] = Field(default_factory=list)
    errors: Optional[str] = None


class CancelAppointmentRequest(BaseModel):
    event_id: str = Field(examples=["_60q30c1060o30e1g60o30hc160o30jbi60o30h9g60o30c1g60o3..."])
    iana_timezone: Optional[str] = Field(default="America/Bogota", examples=["America/Bogota"])


class CancelAppointmentResponse(BaseModel):
    success: bool
    message: Optional[str] = None
    errors: Optional[str] = None


class EditAppointmentRequest(BaseModel):
    event_id: str = Field(examples=["_60q30c1060o30e1g60o30hc160o30jbi60o30h9g60o30c1g60o3..."])
    new_start_date: Optional[str] = Field(default=None, examples=["2026-08-28T10:00:00-05:00"])
    new_duration: Optional[str] = Field(default=None, examples=["30"])
    iana_timezone: Optional[str] = Field(default="America/Bogota", examples=["America/Bogota"])


class EditAppointmentResponse(BaseModel):
    success: bool
    message: Optional[str] = None
    new_start_time: Optional[str] = None
    new_duration: Optional[str] = None
    errors: Optional[str] = None
