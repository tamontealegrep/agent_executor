from fastapi import APIRouter

from tools.babynova_surrogacy.api.v1.endpoints import (
    book_appointment,
    cancel_appointment,
    edit_appointment,
    find_appointment,
    check_documentation,
    health,
    get_available_slots,
    surrogate_classification,
    triage_text,
)
from tools.utils.api.v1.endpoints import calculate_bmi, time_now

api_router = APIRouter()
api_router.include_router(get_available_slots.router, tags=["get_available_slots"])
api_router.include_router(book_appointment.router, tags=["book_appointment"])
api_router.include_router(find_appointment.router, tags=["find_appointment"])
api_router.include_router(cancel_appointment.router, tags=["cancel_appointment"])
api_router.include_router(edit_appointment.router, tags=["edit_appointment"])
api_router.include_router(surrogate_classification.router, tags=["surrogate_classification"])
api_router.include_router(check_documentation.router, tags=["check_documentation"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(triage_text.router, tags=["triage_text"])

# --- Alias snake_case (agent_compiler/agent_runtime) ---
api_router.add_api_route(
    "/get_available_slots", get_available_slots.get_available_slots,
    methods=["POST"], response_model=get_available_slots.SlotsResponse, tags=["get_available_slots"],
)
api_router.add_api_route(
    "/book_appointment", book_appointment.book_appointment,
    methods=["POST"], response_model=book_appointment.BookAppointmentResponse, tags=["book_appointment"],
)
api_router.add_api_route(
    "/find_appointment", find_appointment.find_appointment,
    methods=["POST"], response_model=find_appointment.FindAppointmentResponse, tags=["find_appointment"],
)
api_router.add_api_route(
    "/cancel_appointment", cancel_appointment.cancel_appointment,
    methods=["POST"], response_model=cancel_appointment.CancelAppointmentResponse, tags=["cancel_appointment"],
)
api_router.add_api_route(
    "/edit_appointment", edit_appointment.edit_appointment,
    methods=["POST"], response_model=edit_appointment.EditAppointmentResponse, tags=["edit_appointment"],
)
api_router.add_api_route(
    "/surrogate_classification", surrogate_classification.surrogate_classification,
    methods=["POST"], response_model=surrogate_classification.SurrogateClassificationResponse, tags=["surrogate_classification"],
)
api_router.add_api_route(
    "/check_documentation", check_documentation.check_documentation_sur,
    methods=["POST"], response_model=check_documentation.CheckDocumentationResponse, tags=["check_documentation"],
)
api_router.add_api_route(
    "/calculate_bmi", calculate_bmi.calculate_bmi,
    methods=["POST"], response_model=calculate_bmi.CalculateBmiResponse, tags=["calculate_bmi"],
)
api_router.add_api_route(
    "/time_now", time_now.time_now,
    methods=["POST"], response_model=time_now.TimeNowResponse, tags=["time_now"],
)

# Para agregar un endpoint nuevo:
#   1. crear tools.babynova_surrogacy/api/v1/endpoints/mi_endpoint.py con su APIRouter
#   2. importarlo arriba y aÃ±adir: api_router.include_router(mi_endpoint.router, tags=["mi_endpoint"])

