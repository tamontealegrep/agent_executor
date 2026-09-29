"""
Verifica que main.py (la app raíz) monta cada agente descubierto bajo su
propio prefijo /<slug>/v1/..., el esquema de rutas que usa el deploy real
(un único servidor/proceso para todos los agentes).
"""

from fastapi.testclient import TestClient
import logging

from discovery import agent_slug, discover_agents
from main import SelectiveLogFilter, app

client = TestClient(app)


def test_selective_log_filter_allows_http_and_ghl_endpoint_logs_only():
    log_filter = SelectiveLogFilter()

    http_record = logging.LogRecord(
        name="httpx",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='HTTP Request: POST http://127.0.0.1:8010/family_aims/v1/book-appointment "HTTP/1.1 200 OK"',
        args=(),
        exc_info=None,
    )
    ghl_endpoint_record = logging.LogRecord(
        name="compiled_runner.ghl_endpoint.sam_text",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="[sam_x] Tool book_appointment payload: {'calendar_type': 'SUR'}",
        args=(),
        exc_info=None,
    )
    unrelated_record = logging.LogRecord(
        name="agents.helpers.ghl",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Searching conversation for contact_id=abc",
        args=(),
        exc_info=None,
    )

    assert log_filter.filter(http_record) is True
    assert log_filter.filter(ghl_endpoint_record) is True
    assert log_filter.filter(unrelated_record) is False


def test_selective_log_filter_allows_postgres_connection_warnings():
    """Found live (2026-09-29): a Postgres connection failure in
    upsert_conversation/the closing-message sweep only ever logs through
    compiled_runner.postgres -- silently dropped before, indistinguishable
    from "everything's fine" in Render's logs."""
    log_filter = SelectiveLogFilter()
    postgres_record = logging.LogRecord(
        name="compiled_runner.postgres",
        level=logging.WARNING,
        pathname=__file__,
        lineno=1,
        msg="[some_thread] Failed to upsert conversations row (non-fatal).",
        args=(),
        exc_info=None,
    )
    assert log_filter.filter(postgres_record) is True


def test_composed_app_mounts_novafem_surrogacy_tools_under_its_slug():
    response = client.post("/novafem_surrogacy/v1/surrogate-classification", json={"age": "45"})
    assert response.status_code == 200
    assert response.json()["result"] == "Rejected (Age)"


def test_composed_app_mounts_novafem_surrogacy_check_documentation_under_its_slug():
    response = client.post("/novafem_surrogacy/v1/check-documentation-sur", json={"nationality": "COL"})
    assert response.status_code == 200
    assert response.json()["approved"] is True


def test_composed_app_mounts_utils_tools_under_its_slug():
    response = client.post("/utils/v1/time-now", json={"iana_timezone": "UTC"})
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["iana_timezone"] == "UTC"


def test_composed_app_mounts_utils_check_days_elapsed_under_its_slug():
    response = client.post("/utils/v1/check-days-elapsed", json={"date": "2000/01/01", "days": 365})
    assert response.status_code == 200
    assert response.json()["elapsed"] is True


def test_composed_app_mounts_utils_calculate_bmi_under_its_slug():
    response = client.post("/utils/v1/calculate-bmi", json={"weight_kg": 70, "height_cm": 175})
    assert response.status_code == 200
    assert response.json()["bmi"] == 22.9


def test_composed_app_mounts_utils_request_callback_under_its_slug():
    response = client.post(
        "/utils/v1/request-callback",
        json={"contact_name": "Test", "contact_phone": "+573000000000", "reason": "user_requested", "iana_timezone": "America/Bogota"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["contact_name"] == "Test"


def test_family_aims_callback_alias_matches_the_shared_tool_contract():
    """family_aims_sam_text_2_0's manifest.yaml declares the tool as `callback`
    (shared/tool_contracts/callback_contract_es.yaml), so agent_compiler's
    tool_executor POSTs to /family_aims/v1/callback with `iana_timezone` --
    this alias (and its field name) has to match exactly, or every callback
    attempt 404s or silently drops the timezone (both found live, 2026-09-18)."""
    response = client.post(
        "/family_aims/v1/callback",
        json={
            "contact_name": "Test",
            "contact_phone": "+573000000000",
            "reason": "user_requested",
            "iana_timezone": "America/Bogota",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["contact_name"] == "Test"
    assert body["iana_timezone"] == "America/Bogota"


def test_composed_app_mounts_family_aims_tools_under_its_slug(monkeypatch):
    from tools.family_aims.api.v1.endpoints import get_available_slots as appointments

    # family_aims/.env ya tiene credenciales reales de Google — nunca las
    # ejercitamos en un test automatico. Forzamos que la llamada a Google
    # falle sin tocar la red, y confirmamos que el endpoint degrada al
    # contrato del proyecto (success=false) en vez de un 500.
    async def _no_network(*args, **kwargs):
        raise RuntimeError("este test no debe llamar a Google Calendar de verdad")

    monkeypatch.setattr(appointments, "get_access_token", _no_network)

    ivf = client.post("/family_aims/v1/get-available-slots", json={"calendar_type": "IVF"})
    sur = client.post("/family_aims/v1/get-available-slots", json={"calendar_type": "SUR"})
    assert ivf.status_code == 200
    assert sur.status_code == 200
    assert ivf.json()["success"] is False
    assert sur.json()["success"] is False


def test_composed_app_mounts_family_aims_booking_tools_under_its_slug():
    # book-appointment-* CREA una cita real y manda un correo real si llega a
    # tocar la red — a diferencia de available-slots (solo lectura), acá NUNCA
    # se debe ejercitar con datos que puedan pasar la validación. Se manda una
    # fecha en el pasado a propósito: falla en la primera validación (0
    # llamadas de red) y basta para confirmar que la ruta está montada.
    past_payload = {
        "start_date": "2020-01-01T09:00:00-05:00",
        "duration": "20",
        "contact_name": "Test",
        "contact_email": "test@correo-cliente.com",
        "contact_phone": "+573000000000",
        "language": "ES",
        "iana_timezone": "America/Bogota",
    }
    # Payload IVF (default)
    ivf_payload = past_payload.copy()
    ivf_payload["calendar_type"] = "IVF"
    ivf = client.post("/family_aims/v1/book-appointment", json=ivf_payload)
    
    # Payload SUR
    sur_payload = past_payload.copy()
    sur_payload["calendar_type"] = "SUR"
    sur = client.post("/family_aims/v1/book-appointment", json=sur_payload)
    
    assert ivf.status_code == 200
    assert sur.status_code == 200
    assert ivf.json()["reason"] == "past_date"
    assert sur.json()["reason"] == "past_date"


def test_validation_errors_do_not_echo_request_body():
    invalid_payload = {
        "calendar_type": "SUR",
        "start_date": "2026-09-14T07:00:00-05:00",
        "duration": 30,
        "contact_name": "Test User",
        "contact_email": "test@example.com",
        "contact_phone": "+573000000000",
        "language": "ES",
        "iana_timezone": "Asia/Jakarta",
    }

    response = client.post("/family_aims/v1/book-appointment", json=invalid_payload)

    assert response.status_code == 422
    assert "detail" in response.json()
    assert "body" not in response.json()


def test_echo_reads_allowed_phones_from_env_var(monkeypatch):
    from tools.utils.api.v1.endpoints import echo as echo_endpoint

    monkeypatch.setenv("ECHO_ALLOWED_PHONES", "+573215616921, +573103725324 ,+573016804227")
    echo_endpoint._allowed_phones.cache_clear()

    assert echo_endpoint._allowed_phones() == {
        "+573215616921",
        "+573103725324",
        "+573016804227",
    }

    echo_endpoint._allowed_phones.cache_clear()


def test_composed_app_mounts_novafem_surrogacy_booking_tool_under_its_slug():
    # book-appointment (novafem_surrogacy) tambien crea una cita real y manda un correo
    # real si llega a tocar la red — mismo tratamiento que family_aims:
    # fecha en el pasado, 0 llamadas de red, solo confirma que la ruta monta.
    past_payload = {
        "start_date": "2020-01-01T09:00:00-05:00",
        "duration": "10",
        "contact_name": "Test",
        "contact_email": "test@example.com",
        "contact_phone": "+573000000000",
    }
    response = client.post("/novafem_surrogacy/v1/book-appointment", json=past_payload)
    assert response.status_code == 200
    assert response.json() == {"success": False, "reason": "past_date", "event_id": None, "event_link": None, "meet_link": None, "errors": "La fecha no puede estar en el pasado"}


def test_composed_app_mounts_every_discovered_agent():
    paths = {route.path for route in app.routes}
    for agent_package in discover_agents():
        slug = agent_slug(agent_package)
        assert any(path.startswith(f"/{slug}/v1/") for path in paths), f"no routes mounted for {agent_package}"


def test_composed_app_serves_api_contracts_doc():
    response = client.get("/api-contracts")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "GHL Tools API" in response.text
