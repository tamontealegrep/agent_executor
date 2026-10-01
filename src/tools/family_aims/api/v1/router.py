from fastapi import APIRouter

from tools.family_aims.api.v1.endpoints import (
    get_available_slots,
    book_appointment,
    cancel_appointment,
    edit_appointment,
    find_appointment,
    health,
    visa,
    customer_reply,
    sam_text,
)
from tools.utils.api.v1.endpoints import calculate_bmi, callback_request, time_now

api_router = APIRouter()
api_router.include_router(get_available_slots.router, tags=["get_available_slots"])
api_router.include_router(book_appointment.router, tags=["book_appointment"])
api_router.include_router(find_appointment.router, tags=["find_appointment"])
api_router.include_router(cancel_appointment.router, tags=["cancel_appointment"])
api_router.include_router(edit_appointment.router, tags=["edit_appointment"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(visa.router, tags=["visa_check"])
api_router.include_router(customer_reply.router, tags=["customer_reply"])
api_router.include_router(sam_text.router, tags=["sam_text"])

# Para agregar un endpoint nuevo:
#   1. crear tools.family_aims/api/v1/endpoints/mi_endpoint.py con su APIRouter
#   2. importarlo arriba y añadir: api_router.include_router(mi_endpoint.router, tags=["mi_endpoint"])

# --- Alias snake_case (agent_compiler/agent_runtime) ---
# agent_compiler's tool_executor POSTs to {tools_base_url}/{tool_name} using the
# exact snake_case name declared in each agent's tool_contracts.yaml
# (get_available_slots, book_appointment, find_appointment, cancel_appointment,
# edit_appointment, check_visa) — every endpoint above is hyphenated instead
# (/get-available-slots, etc.), a pre-existing REST-style convention this
# router predates agent_compiler by. Rather than rename the live routes (risk
# to whatever already calls them with hyphens), register the same handler
# functions again under their snake_case tool name — purely additive, the
# hyphenated routes are untouched.
#
# time_now (2026-09-17, found live: family_aims_sam_text_2_0's own
# scheduling.yaml/appointment_management.yaml declare it a required tool,
# but calling it 404'd) is a cross-app case of the same gap: its real
# implementation lives in tools.utils, mounted at /utils/v1/time-now, a
# different top-level prefix than /family_aims/v1 — every compiled agent's
# single tools_base_url only ever points at one prefix (main.py mounts
# each discovered agent's whole tool surface under its own /<slug>/v1/),
# so no alias inside tools.utils's own router could ever have fixed this;
# the alias has to live here, in whichever app tools_base_url actually
# points to. Reuses tools.utils's own handler directly — no duplicated
# logic, single source of truth for the implementation.
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
api_router.add_api_route(
    "/time_now", time_now.time_now,
    methods=["POST"], response_model=time_now.TimeNowResponse, tags=["time_now"],
)
# callback (2026-09-18, found live testing family_aims_sam_text_2_0's
# objections/callback subflow): same cross-app gap as time_now above — the
# real implementation lives in tools.utils, mounted at /utils/v1/request-
# callback, and family_aims_sam_text_2_0's manifest.yaml declares the tool
# as `callback`, so the compiled agent's tool_executor POSTs to
# /family_aims/v1/callback, which never existed under any name. Also found
# in the same pass: tools.utils.schemas.callback_request's `timezone` field
# didn't match the shared tool_contracts/callback_contract_*.yaml's
# `iana_timezone` — renamed the schema field to match the contract, same
# fix already applied to time_now for the same reason.
api_router.add_api_route(
    "/callback", callback_request.request_callback_endpoint,
    methods=["POST"], response_model=callback_request.CallbackRequestResponse, tags=["callback"],
)
api_router.add_api_route(
    "/calculate_bmi", calculate_bmi.calculate_bmi,
    methods=["POST"], response_model=calculate_bmi.CalculateBmiResponse, tags=["calculate_bmi"],
)
