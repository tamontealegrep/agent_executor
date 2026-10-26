"""
Prueba que cada sub-cuenta de GHL usa su propio token y que el location_id elige cuál.
"""

import pytest

from agents.helpers import ghl


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("FA_GHL_TOKEN", "GHL_TOKEN", "BN_GHL_TOKEN", "BN_GHL_LOCATION_ID"):
        monkeypatch.delenv(name, raising=False)
    ghl.default_ghl_client_config.cache_clear()
    ghl.babynova_ghl_client_config.cache_clear()
    yield
    ghl.default_ghl_client_config.cache_clear()
    ghl.babynova_ghl_client_config.cache_clear()


def test_default_config_prefers_fa_token_over_the_legacy_one(monkeypatch):
    monkeypatch.setenv("FA_GHL_TOKEN", "fa-token")
    monkeypatch.setenv("GHL_TOKEN", "legacy-token")

    assert ghl.default_ghl_client_config().token == "fa-token"


def test_default_config_falls_back_to_the_legacy_token(monkeypatch):
    monkeypatch.setenv("GHL_TOKEN", "legacy-token")

    assert ghl.default_ghl_client_config().token == "legacy-token"


def test_babynova_config_never_uses_the_family_aims_token(monkeypatch):
    monkeypatch.setenv("FA_GHL_TOKEN", "fa-token")
    monkeypatch.setenv("GHL_TOKEN", "legacy-token")
    monkeypatch.setenv("BN_GHL_TOKEN", "bn-token")

    assert ghl.babynova_ghl_client_config().token == "bn-token"


def test_location_id_selects_the_token_of_its_sub_account(monkeypatch):
    monkeypatch.setenv("FA_GHL_TOKEN", "fa-token")
    monkeypatch.setenv("BN_GHL_TOKEN", "bn-token")
    monkeypatch.setenv("BN_GHL_LOCATION_ID", "bn-location")

    assert ghl.ghl_client_config_for_location("bn-location").token == "bn-token"
    assert ghl.ghl_client_config_for_location("fa-location").token == "fa-token"
    assert ghl.ghl_client_config_for_location(None).token == "fa-token"


def test_unset_babynova_location_id_never_matches(monkeypatch):
    monkeypatch.setenv("FA_GHL_TOKEN", "fa-token")
    monkeypatch.setenv("BN_GHL_TOKEN", "bn-token")

    assert ghl.ghl_client_config_for_location("").token == "fa-token"
