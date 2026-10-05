import json
from pathlib import Path
from typing import Any, Dict

from instances.helpers.ghl_request import GhlAgentRequest


def load_payload() -> Dict[str, Any]:
    sample_path = Path(__file__).with_name("sample_payload.json")
    if sample_path.exists():
        return json.loads(sample_path.read_text(encoding="utf-8"))
    # Fallback: read from stdin
    import sys
    data = sys.stdin.read()
    return json.loads(data)


def main() -> None:
    payload = load_payload()
    req = GhlAgentRequest.model_validate(payload)

    contact = req.contact or {}
    contact_id = contact.get("contact_id") or req.contact_id
    location_id = contact.get("location_id") or req.location_id

    # This mirrors LM_ACT_SYNC's DO lines, but forces Portuguese as target label
    tool_name = "update_custom_field"
    tool_args = {
        "name": "language",
        "value": "Portuguese",
        "contact_id": contact_id,
        "location_id": location_id,
    }

    print("=== DRY-RUN: Prepared tool call (no HTTP executed) ===")
    print(f"tool: {tool_name}")
    print(json.dumps(tool_args, indent=2, ensure_ascii=False))

    # Extra: show where IDs came from
    print("\n=== Provenance ===")
    print(f"payload.contactId -> request.contact_id: {req.contact_id!r}")
    print(f"payload.location.id/customData.location_id -> request.location_id: {req.location_id!r}")
    print(f"normalized contact object: {json.dumps(contact, indent=2, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
