import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage

DEFAULT_RESET_MARKER = "</>"


def parse_date_added(value: Any) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc)
    if isinstance(value, str):
        normalized = value.strip()
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
    return datetime.min.replace(tzinfo=timezone.utc)


def extract_contact_details(history: List[Dict[str, Any]]) -> Dict[str, str]:
    email_pattern = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)
    phone_pattern = re.compile(r"\+?\d[\d\s\-()]{6,}\d")

    for item in reversed(history):
        if item.get("direction") != "inbound":
            continue
        body = str(item.get("body") or "").strip()
        if not body:
            continue
        email_match = email_pattern.search(body)
        phone_match = phone_pattern.search(body)
        if not email_match and not phone_match:
            continue

        email = email_match.group(0) if email_match else ""
        phone = ""
        if phone_match:
            phone = re.sub(r"\s+", "", phone_match.group(0))

        name_source = body
        if email:
            name_source = name_source.replace(email, " ")
        if phone_match:
            name_source = name_source.replace(phone_match.group(0), " ")
        name = re.sub(r"\s+", " ", name_source).strip(" ,.;")

        if email and phone and name:
            return {
                "contact_name": name,
                "contact_email": email,
                "contact_phone": phone,
            }

    return {}


def find_latest_outbound_message(history: List[Dict[str, Any]]) -> str:
    for item in reversed(history):
        if item.get("direction") == "outbound":
            return str(item.get("body") or "")
    return ""


def history_cutoff(max_days: Optional[int]) -> Optional[datetime]:
    """The cutoff datetime for `max_days`, or None if `max_days` means no limit
    (<= 0 or None). Shared by `filter_recent_messages` and by
    `fetch_messages_async`'s early pagination stop, so both agree on what
    "recent" means.
    """
    if not max_days or max_days <= 0:
        return None
    return datetime.now(timezone.utc) - timedelta(days=max_days)


def filter_recent_messages(history: List[Dict[str, Any]], max_days: Optional[int]) -> List[Dict[str, Any]]:
    """Drops messages older than `max_days`. `max_days` <= 0 or None means no limit."""
    cutoff = history_cutoff(max_days)
    if cutoff is None:
        return history
    return [message for message in history if parse_date_added(message.get("dateAdded")) >= cutoff]


def apply_reset_marker(history: List[Dict[str, Any]], marker: str = DEFAULT_RESET_MARKER) -> List[Dict[str, Any]]:
    """Lets a tester/operator simulate a brand-new conversation from within a real GHL
    thread: an inbound message starting with `marker` (e.g. "</> hola") discards every
    message before it. If the marker appears more than once, the most recent one wins.
    The marker itself is stripped from that message's body before it reaches the LLM.
    """
    reset_index = None
    for index, message in enumerate(history):
        if message.get("direction") != "inbound":
            continue
        if str(message.get("body") or "").strip().startswith(marker):
            reset_index = index

    if reset_index is None:
        return history

    trimmed = [dict(message) for message in history[reset_index:]]
    trimmed[0]["body"] = str(trimmed[0].get("body") or "").strip()[len(marker):].strip()
    return trimmed


def ghl_history_to_langchain(history: List[Dict[str, Any]]) -> List[Any]:
    messages: List[Any] = []
    for item in history:
        role = item.get("direction")
        body = item.get("body", "")
        if not body:
            continue

        if role == "inbound":
            messages.append(HumanMessage(content=body))
        elif role == "outbound":
            messages.append(AIMessage(content=body))
    return messages
