from pathlib import Path

from agents.helpers.ghl_request import GhlAgentRequest


def load_system_prompt(prompt_path: str, default_text: str) -> str:
    if prompt_path:
        path = Path(prompt_path)
        if path.exists():
            return path.read_text(encoding="utf-8")
    return default_text


def prepare_system_prompt(raw_prompt: str, request: GhlAgentRequest) -> str:
    """Replaces {{contact.*}} placeholders in the system prompt with values from the request."""
    contact = getattr(request, "contact", {}) or {}
    contact_name = contact.get("firstName") or contact.get("name") or "Lead"
    contact_email = contact.get("email") or ""
    contact_phone = contact.get("phone") or ""
    contact_lang = (contact.get("language") or "").lower() or "es"

    replacements = {
        "{{contact.name}}": contact_name,
        "{{contact.first_name}}": contact_name.split()[0],
        "{{contact.email}}": contact_email,
        "{{contact.phone}}": contact_phone,
        "{{contact.language}}": contact_lang,
        "[preferred_language]": contact_lang,
    }

    processed = raw_prompt
    for placeholder, value in replacements.items():
        processed = processed.replace(placeholder, str(value))

    return processed
