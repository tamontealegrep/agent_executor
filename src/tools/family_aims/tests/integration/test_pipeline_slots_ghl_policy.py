"""
Prueba que /get-available-slots-ghl aplica las restricciones de horario sobre los slots libres de GHL.
"""

from fastapi.testclient import TestClient

from tools.family_aims.api.v1.endpoints import get_available_slots_ghl as endpoint
from tools.family_aims.main import app

client = TestClient(app)


class _FakeTool:
    ghl_calendar_id = "cal"
    duration_minutes = 30
    days_ahead = 14


class _FakeSettings:
    ghl_token = "token"
    ivf = _FakeTool()


def _free_slot(hour: int, day: str = "2026-10-29") -> str:
    # Hora de Bogotá (UTC-5) expresada en UTC, como la devuelve GHL.
    return f"{day}T{hour + 5:02d}:00:00Z"


def test_ghl_slots_outside_the_window_the_lunch_break_and_holidays_are_dropped(monkeypatch):
    async def _fake_free_slots(**kwargs):
        return [
            _free_slot(6),
            _free_slot(7),
            _free_slot(12),
            _free_slot(13),
            _free_slot(17),
            _free_slot(9, day="2026-11-02"),  # festivo
        ]

    monkeypatch.setattr(endpoint, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(endpoint, "ghl_get_available_slots", _fake_free_slots)

    response = client.post("/api/v1/get-available-slots-ghl", json={"iana_timezone": "America/Bogota", "calendar_type": "IVF"})

    assert response.status_code == 200
    starts = [slot["start_co"] for slot in response.json()["available_slots"]]
    assert [s[11:16] for s in starts] == ["07:00", "13:00"]
