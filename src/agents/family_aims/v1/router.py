from fastapi import APIRouter
from .sam_text import router as sam_text_router
from . import customer_reply as fa_customer_reply

family_agents_router = APIRouter()

# Webhooks / Agents
family_agents_router.include_router(sam_text_router, tags=["family_aims.agents"])
family_agents_router.include_router(fa_customer_reply.router, tags=["family_aims.agents"])
