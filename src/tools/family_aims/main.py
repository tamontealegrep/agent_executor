from fastapi import FastAPI

from tools.family_aims.api.v1.router import api_router

app = FastAPI(title="GHL Tools - Family Aims")

app.include_router(api_router, prefix="/api/v1")
