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
    input_payload as input_payload_endpoint,
)
from tools.utils.schemas.ghl import GhlCustomUpdateResponse
from tools.utils.schemas.input_payload import InputPayloadResponse

api_router = APIRouter()

# --- Shared (utils) ---
api_router.add_api_route(
    "/time-now", time_now.time_now,
    methods=["POST"], response_model=time_now.TimeNowResponse, tags=["utils.time_now"],
)
api_router.add_api_route(
    "/check-days-elapsed", check_days_elapsed.check_days_elapsed,
    methods=["POST"], response_model=check_days_elapsed.CheckDaysElapsedResponse, tags=["utils.check_days_elapsed"],
)
api_router.add_api_route(
    "/callback", callback_request.request_callback_endpoint,
    methods=["POST"], response_model=callback_request.CallbackRequestResponse, tags=["utils.callback"],
)
api_router.add_api_route(
    "/calculate-bmi", calculate_bmi.calculate_bmi,
    methods=["POST"], response_model=calculate_bmi.CalculateBmiResponse, tags=["utils.calculate_bmi"],
)
api_router.add_api_route(
    "/update-custom-field", ucf_endpoint.update_custom_field,
    methods=["POST"], response_model=GhlCustomUpdateResponse, tags=["utils.update_custom_field"],
)
api_router.add_api_route(
    "/echo", echo_endpoint.echo,
    methods=["POST"], tags=["utils.echo"],
)
api_router.add_api_route(
    "/pause", pause_endpoint.pause,
    methods=["POST"], response_model=pause_endpoint.PauseResponse, tags=["utils.pause"],
)

api_router.add_api_route(
    "/input-payload", input_payload_endpoint.receive_payload,
    methods=["POST"], response_model=InputPayloadResponse, tags=["utils.input_payload"],
)

