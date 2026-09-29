"""GhlAgentRequest.fix_ghl_paths -- specifically the contact.language
mapping fixed 2026-09-29 (see its own comment: opening.yaml's
"{{contact.language}}" reference had nothing to read before this).
"""

from agents.helpers.ghl_request import GhlAgentRequest


def _base_payload(**overrides):
    payload = {
        "contact_id": "contact1",
        "locationId": "loc1",
        "message": {"type": 19, "body": "hola"},
        "contact": {"phone": "+573215616921"},
    }
    payload.update(overrides)
    return payload


def test_contact_language_maps_from_root_contact_language_field():
    req = GhlAgentRequest(**_base_payload(contact_language="English"))
    assert req.contact["language"] == "en"


def test_contact_language_maps_from_custom_data_language_field():
    req = GhlAgentRequest(**_base_payload(customData={"language": "Português"}))
    assert req.contact["language"] == "pt"


def test_contact_language_prefers_root_field_over_custom_data():
    req = GhlAgentRequest(**_base_payload(contact_language="English", customData={"language": "Español"}))
    assert req.contact["language"] == "en"


def test_contact_language_is_none_when_nothing_recognized():
    req = GhlAgentRequest(**_base_payload(contact_language="Klingon"))
    assert req.contact["language"] is None


def test_contact_language_is_none_when_no_source_present():
    req = GhlAgentRequest(**_base_payload())
    assert req.contact["language"] is None


def test_real_ghl_payload_maps_english_correctly():
    """The exact real payload shared live (2026-09-29) -- contact_language
    at root and customData.language both say "English"."""
    payload = {
        "contact_language": "English",
        "contact_id": "vWfwGE4zG0YkWeiZtKGJ",
        "full_name": "Tomas Montealegre",
        "email": "montealegre.tomas@outlook.com",
        "phone": "+573215616921",
        "location": {"name": "Family Aims", "id": "vRUkD2IB8Fbbk2G865v3"},
        "message": {"type": 19, "body": "hola"},
        "customData": {
            "location_id": "vRUkD2IB8Fbbk2G865v3",
            "webhook_url": "[[webhook_url]]",
            "language": "English",
        },
    }
    req = GhlAgentRequest(**payload)
    assert req.contact["language"] == "en"
