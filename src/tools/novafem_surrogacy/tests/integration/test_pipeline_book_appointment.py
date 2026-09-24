"""Prueba el endpoint /book-appointment de punta a punta con Google Calendar y Gmail mockeados."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from tools.novafem_surrogacy.api.v1.endpoints import book_appointment as endpoint
from tools.novafem_surrogacy.main import app

client = TestClient(app)


def _next_business_datetime_iso(hour: int = 9, minute: int = 0) -> str:
    now = datetime.now(ZoneInfo("America/Bogota"))
    candidate = (now + timedelta(days=1)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    while candidate.weekday() == 6 or candidate <= now:  # domingo (Python weekday 6) cerrado
        candidate += timedelta(days=1)
    return candidate.isoformat()


def _valid_payload() -> dict:
    return {
        "start_date": _next_business_datetime_iso(),
        "duration": "10",
        "contact_name": "ana   GOMEZ",
        "contact_email": "ana@example.com",
        "contact_phone": "+573007654321",
        "iana_timezone": "America/Bogota",
    }


class _FakeSettings:
    google_client_id = "fake-client-id"
    google_client_secret = "fake-client-secret"
    google_refresh_token = "fake-refresh-token"
    calendar_id = "c_fake@group.calendar.google.com"
    contact_center_email = "contact-center@novafem.com.co"

    class gmail:
        google_client_id = "fake-gmail-id"
        google_client_secret = "fake-gmail-secret"
        google_refresh_token = "fake-gmail-refresh"
        sender_email = "no-reply@novafem.com.co"


class _EmptyCalendarSettings(_FakeSettings):
    google_client_id = ""
    google_client_secret = ""
    google_refresh_token = ""


class _EmptyContactCenterSettings(_FakeSettings):
    contact_center_email = ""


class _EmptyGmailSettings(_FakeSettings):
    class gmail:
        google_client_id = ""
        google_client_secret = "fake-gmail-secret"
        google_refresh_token = "fake-gmail-refresh"
        sender_email = "no-reply@novafem.com.co"


async def _fake_get_access_token(*args, **kwargs):
    return "fake-access-token"


async def _fake_create_event(*args, **kwargs):
    return {"id": "evt789", "htmlLink": "https://calendar.google.com/event?eid=ghi", "hangoutLink": "https://meet.google.com/rst-uvwx"}


async def _fake_create_event_no_id(*args, **kwargs):
    return {}


async def _fake_send_email(*args, **kwargs):
    return {"id": "msg789"}


@pytest.fixture
def mocked_success(monkeypatch):
    monkeypatch.setattr(endpoint, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(endpoint, "get_access_token", _fake_get_access_token)
    monkeypatch.setattr(endpoint, "create_event", _fake_create_event)
    monkeypatch.setattr(endpoint, "send_email", _fake_send_email)


def test_book_appointment_success(mocked_success):
    response = client.post("/api/v1/book-appointment", json=_valid_payload())
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["event_id"] == "evt789"
    assert body["errors"] is None


def test_book_appointment_rejects_past_date(mocked_success):
    payload = _valid_payload()
    payload["start_date"] = "2020-01-01T09:00:00-05:00"
    response = client.post("/api/v1/book-appointment", json=payload)
    body = response.json()
    assert body["success"] is False
    assert body["reason"] == "past_date"


def test_book_appointment_rejects_invalid_start_date(mocked_success):
    payload = _valid_payload()
    payload["start_date"] = "no-es-una-fecha"
    response = client.post("/api/v1/book-appointment", json=payload)
    body = response.json()
    assert body["success"] is False
    assert body["reason"] == "invalid_start_date"


def test_book_appointment_rejects_outside_business_hours(mocked_success):
    payload = _valid_payload()
    payload["start_date"] = _next_business_datetime_iso(hour=20)
    response = client.post("/api/v1/book-appointment", json=payload)
    body = response.json()
    assert body["success"] is False
    assert body["reason"] == "invalid_hour"


def test_book_appointment_rejects_sunday(mocked_success):
    now = datetime.now(ZoneInfo("America/Bogota"))
    days_until_sunday = (6 - now.weekday()) % 7 or 7
    next_sunday = (now + timedelta(days=days_until_sunday)).replace(hour=10, minute=0, second=0, microsecond=0)
    payload = _valid_payload()
    payload["start_date"] = next_sunday.isoformat()
    response = client.post("/api/v1/book-appointment", json=payload)
    body = response.json()
    assert body["success"] is False
    assert body["reason"] == "invalid_hour"


def test_book_appointment_reports_creation_failed(monkeypatch):
    monkeypatch.setattr(endpoint, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(endpoint, "get_access_token", _fake_get_access_token)
    monkeypatch.setattr(endpoint, "create_event", _fake_create_event_no_id)

    response = client.post("/api/v1/book-appointment", json=_valid_payload())
    body = response.json()
    assert body["success"] is False
    assert body["reason"] == "creation_failed"


def test_book_appointment_fails_gracefully_when_calendar_credentials_missing(monkeypatch):
    monkeypatch.setattr(endpoint, "get_settings", lambda: _EmptyCalendarSettings())

    response = client.post("/api/v1/book-appointment", json=_valid_payload())
    body = response.json()
    assert body["success"] is False
    assert "GOOGLE_CLIENT_ID" in body["errors"]


def test_book_appointment_fails_gracefully_when_contact_center_email_missing(monkeypatch):
    monkeypatch.setattr(endpoint, "get_settings", lambda: _EmptyContactCenterSettings())

    response = client.post("/api/v1/book-appointment", json=_valid_payload())
    body = response.json()
    assert body["success"] is False
    assert "CONTACT_CENTER_EMAIL" in body["errors"]


def test_book_appointment_succeeds_even_if_email_fails(monkeypatch):
    monkeypatch.setattr(endpoint, "get_settings", lambda: _EmptyGmailSettings())
    monkeypatch.setattr(endpoint, "get_access_token", _fake_get_access_token)
    monkeypatch.setattr(endpoint, "create_event", _fake_create_event)

    response = client.post("/api/v1/book-appointment", json=_valid_payload())
    body = response.json()
    assert body["success"] is True
    assert body["event_id"] == "evt789"
    assert "contact center" in body["errors"]
