"""
Prueba que el webhook de babynova_surrogate_questions_text queda montado en su ruta y con debounce de 7s.
"""

from agents.babynova.v1 import surrogate_questions_text
from agents.babynova.v1.router import babynova_agents_router


def test_webhook_is_mounted_at_surrogate_questions_text():
    paths = {route.path for route in babynova_agents_router.routes}

    assert "/surrogate-questions-text" in paths


def test_debounce_defaults_to_seven_seconds(monkeypatch):
    monkeypatch.delenv("BABYNOVA_SURROGATE_QUESTIONS_DEBOUNCE_SECONDS", raising=False)

    import importlib

    assert importlib.reload(surrogate_questions_text).DEBOUNCE_SECONDS == 7.0
