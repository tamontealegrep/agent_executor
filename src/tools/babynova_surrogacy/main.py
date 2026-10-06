from fastapi import FastAPI

from tools.babynova_surrogacy.api.v1.router import api_router

app = FastAPI(title="Available Slots API")

app.include_router(api_router, prefix="/api/v1")

