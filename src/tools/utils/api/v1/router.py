from fastapi import APIRouter

from tools.utils.api.v1.endpoints import (
    calculate_bmi,
    callback_request,
    check_days_elapsed,
    echo,
    health,
    time_now,
    input_payload
)

api_router = APIRouter()
api_router.include_router(time_now.router, tags=["time_now"])
api_router.include_router(check_days_elapsed.router, tags=["check_days_elapsed"])
api_router.include_router(calculate_bmi.router, tags=["calculate_bmi"])
api_router.include_router(callback_request.router, tags=["callback_request"])
api_router.include_router(input_payload.router, tags=["input_payload"])
api_router.include_router(echo.router, tags=["echo"])
api_router.include_router(health.router, tags=["health"])

# Para agregar un endpoint nuevo:
#   1. crear tools.utils/api/v1/endpoints/mi_endpoint.py con su APIRouter
#   2. importarlo arriba y añadir: api_router.include_router(mi_endpoint.router, tags=["mi_endpoint"])
