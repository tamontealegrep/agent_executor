"""build_compiled_agent_router is hand-rolled request plumbing (phone
allowlist, idempotency, debounce, the terminal-state-resolves-as-new fix,
run_in_threadpool around graph.invoke) that every compiled agent's GHL
webhook shares -- see ghl_endpoint.py's own module docstring for the live
bugs each piece fixed. None of that was covered by an automated test
before this file.

load_compiled_agent/fresh_state/send_ghl_message_async/upsert_conversation
are monkeypatched throughout: a real turn needs a live OpenAI call and a
real GHL token, which these tests must not depend on. asyncio.run() drives
each scenario end-to-end (request -> background debounce -> threadpool
invoke -> reply) instead of pytest-asyncio, which isn't a project
dependency.
"""

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from langgraph.types import Command

from compiled_runner import ghl_endpoint
from compiled_runner.ghl_endpoint import CompiledAgentGhlConfig, build_compiled_agent_router


class FakeGraph:
    """Stands in for the real checkpointed LangGraph graph. `next`/`values`
    are mutable so a test can simulate a thread that already reached a
    terminal node before the request under test arrives."""

    def __init__(self, values=None, next_=()):
        self.values = values or {}
        self.next = next_
        self.invoke_calls: list[object] = []
        self.reply_prompt = ["Hola, ¿en qué puedo ayudarte?"]

    def get_state(self, config):
        return SimpleNamespace(values=self.values, next=self.next)

    def invoke(self, state_or_command, config):
        self.invoke_calls.append(state_or_command)
        self.values = {**self.values, "current_state": "SOME_STATE"}
        return {"__interrupt__": [SimpleNamespace(value={"prompt": list(self.reply_prompt)})]}


def _payload(contact_id="c1", location_id="loc1", phone="+573215616921", message="Hola", message_id=None):
    body = {
        "contactId": contact_id,
        "locationId": location_id,
        "message": {"type": 19, "body": message},
        "contact": {"phone": phone, "name": "Test"},
    }
    if message_id:
        body["messageId"] = message_id
    return body


def _build_router(monkeypatch, graph, debounce_seconds=0.05, allowed_phones=frozenset({"+573215616921"})):
    upserts = []
    replies = []

    def fake_load_compiled_agent(slug):
        return SimpleNamespace(session_timeout_minutes=None, agent_id=slug), graph

    def fake_fresh_state(artifact, contact=None, seed_history=None, seed_last_user_message=None):
        return {
            "contact": contact,
            "seed_history": seed_history,
            "seed_last_user_message": seed_last_user_message,
        }

    def fake_upsert_conversation(**kwargs):
        upserts.append(kwargs)

    async def fake_send_ghl_message_async(**kwargs):
        replies.append(kwargs)
        return {}

    monkeypatch.setattr(ghl_endpoint, "load_compiled_agent", fake_load_compiled_agent)
    monkeypatch.setattr(ghl_endpoint, "fresh_state", fake_fresh_state)
    monkeypatch.setattr(ghl_endpoint, "upsert_conversation", fake_upsert_conversation)
    monkeypatch.setattr(ghl_endpoint, "send_ghl_message_async", fake_send_ghl_message_async)

    cfg = CompiledAgentGhlConfig(
        slug="test_agent",
        path="/test_agent",
        allowed_phones=allowed_phones,
        debounce_seconds=debounce_seconds,
        ghl_client_config=lambda: object(),
    )
    router = build_compiled_agent_router(cfg)
    app = FastAPI()
    app.include_router(router)
    return app, upserts, replies


def _run(coro):
    return asyncio.run(coro)


def test_non_allowlisted_phone_is_ignored_without_touching_the_graph(monkeypatch):
    def _fail_if_called(slug):
        raise AssertionError("load_compiled_agent must not be called for a non-allowed phone")

    monkeypatch.setattr(ghl_endpoint, "load_compiled_agent", _fail_if_called)
    cfg = CompiledAgentGhlConfig(
        slug="test_agent",
        path="/test_agent",
        allowed_phones=frozenset({"+573215616921"}),
        debounce_seconds=0.05,
        ghl_client_config=lambda: object(),
    )
    app = FastAPI()
    app.include_router(build_compiled_agent_router(cfg))

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            return await client.post("/test_agent", json=_payload(phone="+15555550000"))

    response = _run(scenario())
    assert response.status_code == 202
    assert response.json() == {"status": "ignored", "reason": "phone_not_allowed"}


def test_missing_location_or_contact_id_is_ignored(monkeypatch):
    graph = FakeGraph()
    app, _, _ = _build_router(monkeypatch, graph)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            body = _payload()
            body["locationId"] = None
            return await client.post("/test_agent", json=body)

    response = _run(scenario())
    assert response.status_code == 202
    assert response.json() == {"status": "ignored", "reason": "missing_ids"}


def test_duplicate_execution_id_is_reported_while_still_pending(monkeypatch):
    graph = FakeGraph()
    # A long debounce window so the first request is still "pending" (not
    # yet flushed) when the duplicate arrives.
    app, _, _ = _build_router(monkeypatch, graph, debounce_seconds=5)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            body = _payload(message_id="msg-1")
            first = await client.post("/test_agent", json=body)
            second = await client.post("/test_agent", json=body)
            return first, second

    first, second = _run(scenario())
    assert first.status_code == 202
    assert first.json()["status"] == "accepted"
    assert second.status_code == 202
    assert second.json() == {"status": "duplicate", "execution_id": "msg-1"}


def test_debounce_combines_a_burst_into_one_turn_and_replies(monkeypatch):
    graph = FakeGraph()
    app, upserts, replies = _build_router(monkeypatch, graph, debounce_seconds=0.05)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            r1 = await client.post("/test_agent", json=_payload(message="Hola", message_id="m1"))
            r2 = await client.post("/test_agent", json=_payload(message="Cómo estás", message_id="m2"))
            assert r1.status_code == 202
            assert r2.status_code == 202
            await asyncio.sleep(0.3)  # let the debounce flush + threadpool invoke + reply run

    _run(scenario())

    assert len(graph.invoke_calls) == 1  # one turn, not two
    state = graph.invoke_calls[0]
    assert state["seed_last_user_message"] == "Hola\nCómo estás"

    assert len(replies) == 1
    assert replies[0]["message"] == "Hola, ¿en qué puedo ayudarte?"
    assert replies[0]["contact_id"] == "c1"

    assert len(upserts) == 1
    assert upserts[0]["thread_id"] == "test_agent:loc1:c1"
    assert upserts[0]["agent_slug"] == "test_agent"


def test_new_message_after_a_terminal_node_starts_a_fresh_turn(monkeypatch):
    """snapshot.next == () means the thread already reached a terminal node
    -- resolve_session alone would call this "continuing" (current_state is
    set) and try Command(resume=...), which has nothing to resume. The
    endpoint's own fix must route this through fresh_state instead."""
    graph = FakeGraph(values={"current_state": "SC__SC_END"}, next_=())
    app, _, _ = _build_router(monkeypatch, graph, debounce_seconds=0.05)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/test_agent", json=_payload(message="Otra pregunta", message_id="m3"))
            await asyncio.sleep(0.3)

    _run(scenario())

    assert len(graph.invoke_calls) == 1
    assert isinstance(graph.invoke_calls[0], dict)  # fresh_state's stub output, not a Command
    assert graph.invoke_calls[0]["seed_last_user_message"] == "Otra pregunta"


def test_pending_interrupt_resumes_with_command(monkeypatch):
    """snapshot.next non-empty means the graph is mid-conversation, waiting
    on this exact reply -- must resume via Command, not start fresh."""
    graph = FakeGraph(values={"current_state": "SC__SC_ASK_D"}, next_=("SC__SC_ASK_D",))
    app, _, _ = _build_router(monkeypatch, graph, debounce_seconds=0.05)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/test_agent", json=_payload(message="El martes", message_id="m4"))
            await asyncio.sleep(0.3)

    _run(scenario())

    assert len(graph.invoke_calls) == 1
    assert isinstance(graph.invoke_calls[0], Command)
    assert graph.invoke_calls[0].resume == "El martes"


def test_empty_graph_reply_sends_no_message(monkeypatch):
    graph = FakeGraph()
    graph.reply_prompt = []  # no __interrupt__ prompt text -> nothing to send
    app, upserts, replies = _build_router(monkeypatch, graph, debounce_seconds=0.05)

    async def scenario():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post("/test_agent", json=_payload(message="Hola", message_id="m5"))
            await asyncio.sleep(0.3)

    _run(scenario())

    assert len(upserts) == 1  # the turn still ran and got mirrored
    assert replies == []  # but nothing was sent to GHL


# --- closing-message sweep (WhatsApp's 24h window) -------------------------


def _cfg(**overrides):
    defaults = dict(
        slug="test_agent",
        path="/test_agent",
        allowed_phones=frozenset({"+573215616921"}),
        debounce_seconds=0.05,
        ghl_client_config=lambda: object(),
    )
    defaults.update(overrides)
    return CompiledAgentGhlConfig(**defaults)


def test_closing_message_text_uses_language_specific_default():
    cfg = _cfg()
    assert ghl_endpoint._closing_message_text(cfg, "es").startswith("Gracias")
    assert ghl_endpoint._closing_message_text(cfg, "en").startswith("Thank you")
    assert ghl_endpoint._closing_message_text(cfg, "pt").startswith("Obrigado")


def test_closing_message_text_falls_back_to_spanish_for_unknown_or_missing_language():
    cfg = _cfg()
    assert ghl_endpoint._closing_message_text(cfg, None).startswith("Gracias")
    assert ghl_endpoint._closing_message_text(cfg, "fr").startswith("Gracias")


def test_closing_message_text_string_override_ignores_language():
    cfg = _cfg(closing_message="Custom text")
    assert ghl_endpoint._closing_message_text(cfg, "en") == "Custom text"


def test_closing_message_text_dict_override_is_looked_up_by_language():
    cfg = _cfg(closing_message={"es": "Hola custom", "en": "Hi custom"})
    assert ghl_endpoint._closing_message_text(cfg, "en") == "Hi custom"
    assert ghl_endpoint._closing_message_text(cfg, "fr") == "Hola custom"  # falls back to es


def test_run_closing_sweep_once_sends_and_marks_each_candidate(monkeypatch):
    candidates = [
        {"thread_id": "t1", "contact_id": "c1", "location_id": "l1", "channel": "WHATSAPP", "preferred_language": "es"},
        {"thread_id": "t2", "contact_id": "c2", "location_id": "l1", "channel": "WHATSAPP", "preferred_language": "en"},
    ]
    marked: list[str] = []
    sent: list[dict] = []

    async def fake_send(**kwargs):
        sent.append(kwargs)
        return {}

    monkeypatch.setattr(ghl_endpoint, "find_conversations_needing_closing_message", lambda slug, days: candidates)
    monkeypatch.setattr(ghl_endpoint, "mark_closing_message_sent", marked.append)
    monkeypatch.setattr(ghl_endpoint, "send_ghl_message_async", fake_send)

    count = asyncio.run(ghl_endpoint.run_closing_sweep_once(_cfg()))

    assert count == 2
    assert marked == ["t1", "t2"]
    assert sent[0]["contact_id"] == "c1"
    assert sent[0]["message"].startswith("Gracias")  # es
    assert sent[1]["contact_id"] == "c2"
    assert sent[1]["message"].startswith("Thank you")  # en


def test_run_closing_sweep_once_keeps_going_after_one_candidate_fails(monkeypatch):
    candidates = [
        {"thread_id": "t1", "contact_id": "c1", "location_id": "l1", "channel": "WHATSAPP", "preferred_language": "es"},
        {"thread_id": "t2", "contact_id": "c2", "location_id": "l1", "channel": "WHATSAPP", "preferred_language": "es"},
    ]
    marked: list[str] = []

    async def fake_send(**kwargs):
        if kwargs["contact_id"] == "c1":
            raise RuntimeError("GHL send failed")
        return {}

    monkeypatch.setattr(ghl_endpoint, "find_conversations_needing_closing_message", lambda slug, days: candidates)
    monkeypatch.setattr(ghl_endpoint, "mark_closing_message_sent", marked.append)
    monkeypatch.setattr(ghl_endpoint, "send_ghl_message_async", fake_send)

    count = asyncio.run(ghl_endpoint.run_closing_sweep_once(_cfg()))

    assert count == 1  # only t2 succeeded
    assert marked == ["t2"]  # t1's failure must not be marked sent -- it should be retried next sweep


def test_run_closing_sweep_once_passes_lookback_days_and_slug_through(monkeypatch):
    seen = {}

    def fake_find(slug, lookback_days):
        seen["slug"] = slug
        seen["lookback_days"] = lookback_days
        return []

    monkeypatch.setattr(ghl_endpoint, "find_conversations_needing_closing_message", fake_find)

    asyncio.run(ghl_endpoint.run_closing_sweep_once(_cfg(slug="babynova_triage_obstetrico_text", closing_message_lookback_days=5.0)))

    assert seen == {"slug": "babynova_triage_obstetrico_text", "lookback_days": 5.0}


def test_closing_sweep_starts_only_when_postgres_is_enabled(monkeypatch):
    """The startup hook must not spin up the sweep loop at all when
    DATABASE_URL is unset -- nothing for it to read anyway."""
    started: list[object] = []

    async def fake_loop(cfg):
        started.append(cfg)

    monkeypatch.setattr(ghl_endpoint, "_closing_sweep_loop", fake_loop)

    for enabled in (False, True):
        started.clear()
        monkeypatch.setattr(ghl_endpoint, "postgres_enabled", lambda: enabled)
        app = FastAPI()
        app.include_router(build_compiled_agent_router(_cfg(slug=f"sweep_test_{enabled}")))

        # Run the app's registered startup handlers directly -- the same
        # mechanism uvicorn triggers on real boot -- rather than a full
        # ASGI lifespan protocol dance, since that's the one thing under
        # test here (whether the sweep loop gets scheduled at all).
        async def run_startup():
            for handler in app.router.on_startup:
                await handler()
            await asyncio.sleep(0)  # let asyncio.create_task's scheduled task actually run once

        asyncio.run(run_startup())
        assert len(started) == (1 if enabled else 0)
