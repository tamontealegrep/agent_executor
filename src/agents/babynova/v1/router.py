from fastapi import APIRouter
from .surrogate_questions_text import router as surrogate_questions_router
from .triage_text import router as triage_router

babynova_agents_router = APIRouter()

# Webhooks / Agents
babynova_agents_router.include_router(triage_router, tags=["babynova.agents"])
babynova_agents_router.include_router(surrogate_questions_router, tags=["babynova.agents"])
