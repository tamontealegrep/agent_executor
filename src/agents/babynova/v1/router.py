from fastapi import APIRouter
from .triage_text import router as triage_router

babynova_agents_router = APIRouter()

# Webhooks / Agents
babynova_agents_router.include_router(triage_router, tags=["babynova.agents"])
