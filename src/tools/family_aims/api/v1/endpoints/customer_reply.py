import os
import traceback
from typing import Any, Dict, List, Optional

import requests
from fastapi import APIRouter, HTTPException
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, SystemMessage

from instances.helpers.ghl import GHL_OUTBOUND_CHANNEL_MAP
from instances.helpers.ghl_request import GhlAgentRequest
from instances.helpers.text import normalize_channel

router = APIRouter()

# Configuración básica
BASE_URL = os.getenv("GHL_BASE_URL", "https://services.leadconnectorhq.com").rstrip("/")
GHL_VERSION = os.getenv("GHL_VERSION", "v3")

def _resolve_ghl_token() -> str:
    return (os.getenv("GHL_TOKEN") or "").strip()

def _build_headers() -> Dict[str, str]:
    token = _resolve_ghl_token()
    return {
        "Authorization": f"Bearer {token}",
        "Version": GHL_VERSION,
        "Content-Type": "application/json"
    }

def _send_ghl_message(
    contact_id: str,
    message: str,
    channel: str = "SMS",
    location_id: Optional[str] = None,
    reply_message_id: Optional[str] = None,
) -> Dict[str, Any]:
    url = f"{BASE_URL}/conversations/messages"
    
    channel_key = normalize_channel(channel)
    send_type = GHL_OUTBOUND_CHANNEL_MAP.get(channel_key, "Live_Chat")

    payload = {
        "contactId": contact_id,
        "type": send_type,
        "message": message,
        "status": "pending"
    }
    if location_id:
        payload["locationId"] = location_id
    if reply_message_id:
        payload["replyMessageId"] = reply_message_id

    resp = requests.post(url, json=payload, headers=_build_headers())
    resp.raise_for_status()
    return resp.json()

@router.post("/customer-reply")
async def customer_reply(payload: Dict[str, Any]):
    try:
        # Usar GhlAgentRequest para normalizar la entrada
        req = GhlAgentRequest(**payload)
        
        contact_id = req.contact_id
        location_id = req.location_id
        message_id = req.messageId
        user_message = req.message
        channel = req.channel or "LIVE_CHAT"

        if not contact_id or not user_message:
            raise HTTPException(status_code=400, detail="Missing required fields (contact_id or message)")

        # Initialize LangChain LLM
        llm = ChatOpenAI(model="gpt-4o", temperature=0.7)
        
        messages = [
            SystemMessage(content="You are a helpful assistant for Family Aims, a surrogacy and IVF agency. Respond professionally and empathetically."),
            HumanMessage(content=user_message)
        ]
        
        ai_response = llm.invoke(messages)
        reply_text = ai_response.content

        # Enviar respuesta a GHL
        sent_status = _send_ghl_message(
            contact_id,
            reply_text,
            channel=channel,
            location_id=location_id,
            reply_message_id=message_id,
        )

        return {
            "ok": True,
            "contact_id": contact_id,
            "ai_response": reply_text,
            "ghl_status": sent_status
        }

    except Exception as e:
        error_trace = traceback.format_exc()
        return {
            "ok": False,
            "error": str(e),
            "trace": error_trace
        }
