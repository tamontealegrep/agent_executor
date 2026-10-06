from fastapi import APIRouter

from tools.family_aims.api.v1.endpoints import (
    get_available_slots,
    book_appointment,
    cancel_appointment,
    edit_appointment,
    find_appointment,
    health,
    check_visa,
    customer_reply,
    sam_text,
)
# No cross-app aliasing: utils endpoints live under /tools/v1 exclusively.

api_router = APIRouter()
api_router.include_router(get_available_slots.router, tags=["get_available_slots"])
api_router.include_router(book_appointment.router, tags=["book_appointment"])
api_router.include_router(find_appointment.router, tags=["find_appointment"])
api_router.include_router(cancel_appointment.router, tags=["cancel_appointment"])
api_router.include_router(edit_appointment.router, tags=["edit_appointment"])
api_router.include_router(health.router, tags=["health"])
api_router.include_router(check_visa.router, tags=["visa_check"])
api_router.include_router(customer_reply.router, tags=["customer_reply"])
api_router.include_router(sam_text.router, tags=["sam_text"])

# Para agregar un endpoint nuevo:
#   1. crear tools.family_aims/api/v1/endpoints/mi_endpoint.py con su APIRouter
#   2. importarlo arriba y añadir: api_router.include_router(mi_endpoint.router, tags=["mi_endpoint"])

## No snake_case aliases — only kebab-case endpoints defined in each module's router.
