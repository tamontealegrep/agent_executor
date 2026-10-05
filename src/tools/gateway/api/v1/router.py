from fastapi import APIRouter

# Utils tools (shared across agents)
from tools.utils.api.v1.endpoints import (
    time_now,
    callback_request,
    check_days_elapsed,
    calculate_bmi,
    pause as pause_endpoint,
    update_custom_field as ucf_endpoint,
    echo as echo_endpoint,
)
from tools.utils.schemas.ghl import GhlCustomUpdateResponse

# Family Aims tools (vertical-specific, aggregated here under unified base)
from tools.family_aims.api.v1.endpoints import (
    get_available_slots,
    book_appointment,
    find_appointment,
    cancel_appointment,
    edit_appointment,
    visa,
)
from tools.novafem_surrogacy.api.v1.endpoints import (
    get_available_slots as nf_get_available_slots,
    book_appointment as nf_book_appointment,
    find_appointment as nf_find_appointment,
    cancel_appointment as nf_cancel_appointment,
    edit_appointment as nf_edit_appointment,
    surrogate_classification as nf_surrogate_classification,
    check_documentation as nf_check_documentation,
)

api_router = APIRouter()

# --- Shared (utils) under snake_case names ---
api_router.add_api_route(
    "/time_now", time_now.time_now,
    methods=["POST"], response_model=time_now.TimeNowResponse, tags=["time_now"],
)
api_router.add_api_route(
    "/check_days_elapsed", check_days_elapsed.check_days_elapsed,
    methods=["POST"], response_model=check_days_elapsed.CheckDaysElapsedResponse, tags=["check_days_elapsed"],
)
api_router.add_api_route(
    "/callback", callback_request.request_callback_endpoint,
    methods=["POST"], response_model=callback_request.CallbackRequestResponse, tags=["callback"],
)
api_router.add_api_route(
    "/calculate_bmi", calculate_bmi.calculate_bmi,
    methods=["POST"], response_model=calculate_bmi.CalculateBmiResponse, tags=["calculate_bmi"],
)
api_router.add_api_route(
    "/update_custom_field", ucf_endpoint.update_custom_field,
    methods=["POST"], response_model=GhlCustomUpdateResponse, tags=["update_custom_field"],
)
api_router.add_api_route(
    "/echo", echo_endpoint.echo,
    methods=["POST"], tags=["echo"],
)
api_router.add_api_route(
    "/pause", pause_endpoint.pause,
    methods=["POST"], response_model=pause_endpoint.PauseResponse, tags=["pause"],
)

# --- Family Aims vertical under snake_case names ---
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
    "/check_visa", visa.check_visa_requirement,
    methods=["POST"], response_model=visa.VisaCheckResponse, tags=["visa_check"],
)

# --- Novafem Surrogacy vertical under snake_case names ---
api_router.add_api_route(
    "/nf_get_available_slots", nf_get_available_slots.get_available_slots,
    methods=["POST"], response_model=nf_get_available_slots.SlotsResponse, tags=["novafem.get_available_slots"],
)
api_router.add_api_route(
    "/nf_book_appointment", nf_book_appointment.book_appointment,
    methods=["POST"], response_model=nf_book_appointment.BookAppointmentResponse, tags=["novafem.book_appointment"],
)
api_router.add_api_route(
    "/nf_find_appointment", nf_find_appointment.find_appointment,
    methods=["POST"], response_model=nf_find_appointment.FindAppointmentResponse, tags=["novafem.find_appointment"],
)
api_router.add_api_route(
    "/nf_cancel_appointment", nf_cancel_appointment.cancel_appointment,
    methods=["POST"], response_model=nf_cancel_appointment.CancelAppointmentResponse, tags=["novafem.cancel_appointment"],
)
api_router.add_api_route(
    "/nf_edit_appointment", nf_edit_appointment.edit_appointment,
    methods=["POST"], response_model=nf_edit_appointment.EditAppointmentResponse, tags=["novafem.edit_appointment"],
)
api_router.add_api_route(
    "/nf_surrogate_classification", nf_surrogate_classification.surrogate_classification,
    methods=["POST"], response_model=nf_surrogate_classification.SurrogateClassificationResponse, tags=["novafem.surrogate_classification"],
)
api_router.add_api_route(
    "/nf_check_documentation", nf_check_documentation.check_documentation,
    methods=["POST"], response_model=nf_check_documentation.CheckDocumentationResponse, tags=["novafem.check_documentation"],
)
