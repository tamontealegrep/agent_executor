from fastapi import FastAPI

from tools.utils.api.v1.router import api_router

app = FastAPI(title="GHL Tools - Utils")

app.include_router(api_router, prefix="/api/v1")
