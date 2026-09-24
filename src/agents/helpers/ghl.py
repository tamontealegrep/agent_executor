import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from agents.helpers.history import filter_recent_messages, history_cutoff, parse_date_added
from agents.helpers.text import normalize_channel

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

GHL_INBOUND_TYPE_MAP: Dict[Any, str] = {
    # ── NUMÉRICOS — verificados con payloads reales + docs oficiales ──────
    1: "CALL",
    2: "SMS",
    5: "LIVE_CHAT",
    10: "VOICEMAIL",
    11: "FACEBOOK",
    18: "IG",
    19: "WHATSAPP",
    # EMAIL y GMB → pendientes de payload real

    # ── TYPE_ STRINGS — docs oficiales API 2023-02-21 ────────────────────
    # (filter options del endpoint get-messages)
    "TYPE_CALL": "CALL",
    "TYPE_SMS": "SMS",
    "TYPE_EMAIL": "EMAIL",
    "TYPE_FACEBOOK": "FACEBOOK",
    "TYPE_GMB": "GMB",
    "TYPE_INSTAGRAM": "IG",
    "TYPE_WHATSAPP": "WHATSAPP",
    "TYPE_ACTIVITY_APPOINTMENT": "ACTIVITY_APPOINTMENT",
    "TYPE_ACTIVITY_CONTACT": "ACTIVITY_CONTACT",
    "TYPE_ACTIVITY_INVOICE": "ACTIVITY_INVOICE",
    "TYPE_ACTIVITY_OPPORTUNITY": "ACTIVITY_OPPORTUNITY",
    "TYPE_ACTIVITY_PAYMENT": "ACTIVITY_PAYMENT",

    # ── TYPE_ STRINGS — docs oficiales API 2021-07-28 ────────────────────
    "TYPE_WEBCHAT": "LIVE_CHAT",  # ✅ confirmado 2021 docs
    "TYPE_CAMPAIGN_SMS": "SMS",
    "TYPE_CAMPAIGN_CALL": "CALL",
    "TYPE_CAMPAIGN_EMAIL": "EMAIL",
    "TYPE_CAMPAIGN_VOICEMAIL": "VOICEMAIL",
    "TYPE_CAMPAIGN_FACEBOOK": "FACEBOOK",
    "TYPE_SMS_REVIEW_REQUEST": "SMS",
    "TYPE_SMS_NO_SHOW_REQUEST": "SMS",

    # ── TYPE_ STRINGS — confirmados en webhook examples (messageTypeString)
    "TYPE_VOICEMAIL": "VOICEMAIL",  # ✅ OutboundMessage + InboundMessage webhook

    # ── Defensivos — tab "Live Chat" existe en webhook page pero string
    #    exacto no confirmable desde HTML estático (2021 usa TYPE_WEBCHAT)
    "TYPE_LIVE_CHAT": "LIVE_CHAT",  # ⚠️ mantener como fallback

    # ── Nuevo — OutboundMessage webhook docs (notas internas agentes)
    "InternalComment": "INTERNAL",  # ✅ OutboundMessage webhook page
}

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


def get_ghl_headers(config: GhlClientConfig) -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {config.token}",
        "Version": config.version,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


async def fetch_messages_async(conversation_id: str, config: GhlClientConfig) -> List[Dict[str, Any]]:
    """Fetches GHL conversation messages, paginating newest-to-oldest via
    `lastMessageId` (confirmed empirically against the real API: each page is
    strictly older than the previous one). Stops as soon as a page contains a
    message older than `history_max_days`, instead of always paginating the
    entire conversation before filtering - for a long-running contact this is
    the difference between 1-2 requests and dozens.
    """
    last_message_id = None
    all_messages: List[Dict[str, Any]] = []
    headers = get_ghl_headers(config)
    cutoff = history_cutoff(config.history_max_days)

    async with httpx.AsyncClient(timeout=config.history_timeout_seconds) as client:
        while True:
            url = f"{config.base_url}/conversations/{conversation_id}/messages?limit={config.message_history_page_size}"
            if last_message_id:
                url += f"&lastMessageId={last_message_id}"

            response = await client.get(url, headers=headers)
            response.raise_for_status()
            data = response.json()

            container = data.get("messages", {}) or {}
            page_messages = container.get("messages", []) or []
            all_messages.extend(page_messages)

            next_page = container.get("nextPage", False)
            last_message_id = container.get("lastMessageId")

            reached_cutoff = cutoff is not None and any(
                parse_date_added(message.get("dateAdded")) < cutoff for message in page_messages
            )

            if not next_page or not last_message_id or reached_cutoff:
                break

    all_messages.sort(key=lambda message: parse_date_added(message.get("dateAdded")))
    return filter_recent_messages(all_messages, config.history_max_days)


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
