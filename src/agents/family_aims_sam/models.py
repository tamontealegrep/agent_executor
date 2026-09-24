from pydantic import BaseModel

from agents.helpers.ghl_request import GhlAgentRequest


class SamRequest(GhlAgentRequest):
    pass


class SamResponse(BaseModel):
    status: str = "accepted"
    execution_id: str
