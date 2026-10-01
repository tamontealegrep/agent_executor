import json
import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from instances.helpers.text import normalize_channel

# agent_executor root: src/agents/helpers/ghl.py -> helpers -> agents -> src -> root
GHL_DEFAULTS_PATH = Path(__file__).resolve().parents[3] / "config" / "ghl_defaults.json"

GHL_OUTBOUND_CHANNEL_MAP: Dict[str, str] = {
    "SMS": "SMS",
    "WHATSAPP": "WhatsApp",
    "IG": "IG",
    "INSTAGRAM": "IG",
    "EMAIL": "Email",
    "FACEBOOK": "FB",
    "GMB": "FB",
    "LIVE_CHAT": "Live_Chat",
}

def _load_inbound_type_map() -> Dict[Any, str]:
    """Incoming message.type -> normalized channel name, from
    config/ghl_defaults.json's inbound_type_map (edit that file, not this
    function -- see its own "_note" field for provenance/confidence).
    Numeric-looking keys ("19") are parsed back to int: a real GHL
    payload's message.type for those entries is a JSON number, not a
    string, but JSON object keys can only be strings, so the file spells
    them as "19" and this loader turns that back into 19 to match.
    """
    raw = json.loads(GHL_DEFAULTS_PATH.read_text(encoding="utf-8")).get("inbound_type_map", {})
    return {(int(key) if key.isdigit() else key): value for key, value in raw.items() if key != "_note"}


GHL_INBOUND_TYPE_MAP: Dict[Any, str] = _load_inbound_type_map()

GHL_FILTER_TYPE_MAP: Dict[str, str] = {
    "CALL": "TYPE_CALL",
    "SMS": "TYPE_SMS",
    "WHATSAPP": "TYPE_WHATSAPP",
    "IG": "TYPE_INSTAGRAM",
    "INSTAGRAM": "TYPE_INSTAGRAM",
    "EMAIL": "TYPE_EMAIL",
    "FACEBOOK": "TYPE_FACEBOOK",
    "GMB": "TYPE_GMB",
    "VOICEMAIL": "TYPE_VOICEMAIL",
    "LIVE_CHAT": "TYPE_LIVE_CHAT",  # ✅ TYPE_WEBCHAT también es válido según docs 2021
}


@dataclass(frozen=True)
class GhlClientConfig:
    base_url: str
    version: str
    token: str
    message_history_page_size: int
    conversation_search_limit: int
    history_timeout_seconds: float
    conversation_search_timeout_seconds: float
    send_message_timeout_seconds: float
    default_send_type: str
    channel_type_map: Dict[str, str]
    history_max_days: int


def build_ghl_client_config(
    ghl_settings: Dict[str, Any],
    runtime_settings: Dict[str, Any],
    messaging_settings: Dict[str, Any],
) -> GhlClientConfig:
    timeouts = runtime_settings.get("timeouts", {})
    base_url_env = ghl_settings.get("base_url_env", "GHL_BASE_URL")
    version_env = ghl_settings.get("version_env", "GHL_VERSION")
    history_max_days_env = ghl_settings.get("history_max_days_env", "GHL_HISTORY_MAX_DAYS")
    history_max_days_raw = os.getenv(history_max_days_env, "").strip()
    history_max_days = int(history_max_days_raw) if history_max_days_raw else int(ghl_settings.get("history_max_days", 7))
    return GhlClientConfig(
        base_url=os.getenv(base_url_env, ghl_settings.get("default_base_url", "https://services.leadconnectorhq.com")).rstrip("/"),
        version=os.getenv(version_env, ghl_settings.get("default_version", "v3")),
        token=os.getenv("GHL_TOKEN", ""),
        message_history_page_size=int(ghl_settings.get("message_history_page_size", 100)),
        conversation_search_limit=int(ghl_settings.get("conversation_search_limit", 1)),
        history_timeout_seconds=float(timeouts.get("history_seconds", 60.0)),
        conversation_search_timeout_seconds=float(timeouts.get("conversation_search_seconds", 30.0)),
        send_message_timeout_seconds=float(timeouts.get("send_message_seconds", 30.0)),
        default_send_type=messaging_settings.get("default_send_type", "Live_Chat"),
        channel_type_map=messaging_settings.get("channel_type_map", GHL_OUTBOUND_CHANNEL_MAP),
        history_max_days=history_max_days,
    )


@lru_cache
def default_ghl_client_config() -> GhlClientConfig:
    """The agent-agnostic GhlClientConfig, read from config/ghl_defaults.json
    at the project root -- edit that file directly for base_url, timeouts,
    or the channel map; no code change needed.

    For any agent whose GHL wiring doesn't need its own overrides (every
    compiled agent so far -- see compiled_runner/ghl_endpoint.py). The
    classic family_aims_sam agent keeps its own copy in agent.json instead,
    since it also carries agent-specific runtime keys (allowed_phones, tool
    timeouts) this file deliberately doesn't.
    """
    raw = json.loads(GHL_DEFAULTS_PATH.read_text(encoding="utf-8"))
    return build_ghl_client_config(
        raw.get("ghl", {}),
        raw.get("runtime", {}),
        raw.get("messaging", {}),
    )


def get_ghl_headers(config: GhlClientConfig) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {config.token}",
        "Version": config.version,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def search_conversation_async(
    contact_id: str,
    location_id: str,
    config: GhlClientConfig,
    logger: Optional[logging.Logger] = None,
) -> Optional[str]:
    active_logger = logger or logging.getLogger(__name__)
    url = (
        f"{config.base_url}/conversations/search?locationId={location_id}"
        f"&contactId={contact_id}&sort=desc&limit={config.conversation_search_limit}"
    )
    headers = get_ghl_headers(config)
    active_logger.info("Searching conversation for contact_id=%s location_id=%s", contact_id, location_id)

    async with httpx.AsyncClient(timeout=config.conversation_search_timeout_seconds) as client:
        try:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            conversations = response.json().get("conversations", [])
            if conversations:
                active_logger.info("Found %s conversation(s) for contact_id=%s", len(conversations), contact_id)
                return conversations[0].get("id")
        except Exception as exc:
            active_logger.error("Error searching conversation: %s", exc)
    return None


async def send_ghl_message_async(
    contact_id: str,
    message: str,
    channel: Optional[str],
    exec_id: str,
    config: GhlClientConfig,
    location_id: Optional[str] = None,
    reply_message_id: Optional[str] = None,
    logger: Optional[logging.Logger] = None,
) -> Dict[str, Any]:
    active_logger = logger or logging.getLogger(__name__)
    send_type = config.channel_type_map.get(normalize_channel(channel), config.default_send_type)
    payload = {
        "contactId": contact_id,
        "type": send_type,
        "message": message,
        "status": "pending",
    }
    if location_id:
        payload["locationId"] = location_id
    if reply_message_id:
        payload["replyMessageId"] = reply_message_id

    url = f"{config.base_url}/conversations/messages"

    active_logger.info(
        "[%s] Sending outbound GHL message: contact_id=%s channel=%s mapped_type=%s size_chars=%s location_id=%s",
        exec_id,
        contact_id,
        channel,
        send_type,
        len(message or ""),
        location_id,
    )

    async with httpx.AsyncClient(timeout=config.send_message_timeout_seconds) as client:
        response = await client.post(url, json=payload, headers=get_ghl_headers(config))
        response.raise_for_status()
        body = response.json()
        active_logger.info("[%s] GHL outbound message accepted.", exec_id)
        return body
