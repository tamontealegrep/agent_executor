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
    get_available_slots as fa_get_available_slots,
    book_appointment as fa_book_appointment,
    find_appointment as fa_find_appointment,
    cancel_appointment as fa_cancel_appointment,
    edit_appointment as fa_edit_appointment,
    check_visa as fa_check_visa,
)
from tools.babynova_surrogacy.api.v1.endpoints import (
    get_available_slots as bn_get_available_slots_sur,
    book_appointment as bn_book_appointment_sur,
    find_appointment as bn_find_appointment_sur,
    cancel_appointment as bn_cancel_appointment_sur,
    edit_appointment as bn_edit_appointment_sur,
    surrogate_classification as bn_surrogate_classification_sur,
    check_documentation as bn_check_documentation_sur,
)

api_router = APIRouter()

# --- Shared (utils) ---
api_router.add_api_route(
    "/time-now", time_now.time_now,
    methods=["POST"], response_model=time_now.TimeNowResponse, tags=["time_now"],
)
api_router.add_api_route(
    "/check-days-elapsed", check_days_elapsed.check_days_elapsed,
    methods=["POST"], response_model=check_days_elapsed.CheckDaysElapsedResponse, tags=["check_days_elapsed"],
)
api_router.add_api_route(
    "/callback", callback_request.request_callback_endpoint,
    methods=["POST"], response_model=callback_request.CallbackRequestResponse, tags=["callback"],
)
api_router.add_api_route(
    "/calculate-bmi", calculate_bmi.calculate_bmi,
    methods=["POST"], response_model=calculate_bmi.CalculateBmiResponse, tags=["calculate_bmi"],
)
api_router.add_api_route(
    "/update-custom-field", ucf_endpoint.update_custom_field,
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

# --- Babynova Surrogacy ---
babynova_api_router = APIRouter()

babynova_api_router.add_api_route(
    "/check-documentation-sur", bn_check_documentation_sur.check_documentation,
    methods=["POST"], response_model=bn_check_documentation_sur.CheckDocumentationResponse, tags=["babynova_sur.check_documentation"],
)
babynova_api_router.add_api_route(
    "/surrogate-classification-sur", bn_surrogate_classification_sur.surrogate_classification,
    methods=["POST"], response_model=bn_surrogate_classification_sur.SurrogateClassificationResponse, tags=["babynova_sur.surrogate_classification"],
)
babynova_api_router.add_api_route(
    "/book-appointment-sur", bn_book_appointment_sur.book_appointment,
    methods=["POST"], response_model=bn_book_appointment_sur.BookAppointmentResponse, tags=["babynova_sur.book_appointment"],
)
babynova_api_router.add_api_route(
    "/cancel-appointment-sur", bn_cancel_appointment_sur.cancel_appointment,
    methods=["POST"], response_model=bn_cancel_appointment_sur.CancelAppointmentResponse, tags=["babynova_sur.cancel_appointment"],
)
babynova_api_router.add_api_route(
    "/edit-appointment-sur", bn_edit_appointment_sur.edit_appointment,
    methods=["POST"], response_model=bn_edit_appointment_sur.EditAppointmentResponse, tags=["babynova_sur.edit_appointment"],
)
babynova_api_router.add_api_route(
    "/get-available-slots-sur", bn_get_available_slots_sur.get_available_slots,
    methods=["POST"], response_model=bn_get_available_slots_sur.SlotsResponse, tags=["babynova_sur.get_available_slots"],
)
babynova_api_router.add_api_route(
    "/find-appointment-sur", bn_find_appointment_sur.find_appointment,
    methods=["POST"], response_model=bn_find_appointment_sur.FindAppointmentResponse, tags=["babynova_sur.find_appointment"],
)

# --- Family Aims endpoints (to be mounted at /family_aims/v1) ---
family_api_router = APIRouter()

family_api_router.add_api_route(
    "/get-available-slots", fa_get_available_slots.get_available_slots,
    methods=["POST"], response_model=fa_get_available_slots.SlotsResponse, tags=["family_aims.get_available_slots"],
)
family_api_router.add_api_route(
    "/book-appointment", fa_book_appointment.book_appointment,
    methods=["POST"], response_model=fa_book_appointment.BookAppointmentResponse, tags=["family_aims.book_appointment"],
)
family_api_router.add_api_route(
    "/find-appointment", fa_find_appointment.find_appointment,
    methods=["POST"], response_model=fa_find_appointment.FindAppointmentResponse, tags=["family_aims.find_appointment"],
)
family_api_router.add_api_route(
    "/cancel-appointment", fa_cancel_appointment.cancel_appointment,
    methods=["POST"], response_model=fa_cancel_appointment.CancelAppointmentResponse, tags=["family_aims.cancel_appointment"],
)
family_api_router.add_api_route(
    "/edit-appointment", fa_edit_appointment.edit_appointment,
    methods=["POST"], response_model=fa_edit_appointment.EditAppointmentResponse, tags=["family_aims.edit_appointment"],
)
family_api_router.add_api_route(
    "/check-visa", fa_check_visa.check_visa_requirement,
    methods=["POST"], response_model=fa_check_visa.VisaCheckResponse, tags=["family_aims.visa_check"],
)

