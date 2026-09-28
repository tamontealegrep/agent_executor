"""Loads a compiled agent_compiler agent (graph.json + assets.json) and
builds a real, checkpointed LangGraph graph for it via agent_runtime.

Pilot / local-testing module (2026-09-16) — proves the agent_compiler ->
agent_runtime -> agent_executer pipeline end to end before any GHL wiring.
Not yet wired into main.py's normal src/tools/* discovery: see
compiled_runner/endpoint.py for the router this feeds.
"""

from __future__ import annotations

import contextvars
import json
import os
import sqlite3
import threading
from pathlib import Path

from agent_compiler.runtime.graph_builder import build_graph
from agent_compiler.runtime.llm_client import OpenAILLMClient
from agent_compiler.targets.langgraph.runtime_artifact import RuntimeArtifact, runtime_artifact_from_dict
from langgraph.checkpoint.sqlite import SqliteSaver

from compiled_runner.postgres import build_postgres_checkpointer, postgres_enabled

COMPILED_AGENTS_DIR = Path(__file__).resolve().parents[2] / "compiled_agents"

# say_callback is fixed once, at build_graph() time, not per request — a
# message-only node (message/terminal/handler/FAQ) has no other way to
# surface its text (see agent_runtime's USAGE.md §8.1). A ContextVar keeps
# this isolated and concurrency-safe per request; endpoint.py sets a fresh
# list before every invoke() and reads it back after.
message_buffer: contextvars.ContextVar[list[str]] = contextvars.ContextVar("message_buffer")


def _say_callback(text: str) -> None:
    message_buffer.get().append(text)


# Which src/tools/<app>/ backend a compiled agent's tool calls land on.
# Found live (2026-09-28) while wiring babynova_triage_obstetrico_text:
# every compiled agent was hardcoded to /family_aims/v1 regardless of slug,
# so babynova_surrogate_questions_voice's get_available_slots/book_appointment/
# etc (which DO exist, under novafem_surrogacy) were silently 404ing against
# the wrong app. contacto_ap/citas_prioritarias (babynova_triage_obstetrico_text)
# don't exist under any app yet -- novafem_surrogacy is the closest home for
# them (same business vertical) but they still need to be implemented there;
# see TODO.md.
_TOOLS_APP_BY_SLUG: dict[str, str] = {
    "family_aims_sam_text": "family_aims",
    "family_aims_sam_en_voice": "family_aims",
    "family_aims_sam_es_voice": "family_aims",
    "family_aims_sam_pt_voice": "family_aims",
    "babynova_surrogate_questions_voice": "novafem_surrogacy",
    "babynova_triage_obstetrico_text": "novafem_surrogacy",
}


def _tools_base_url(slug: str) -> str:
    """Where this compiled agent's tool calls land -- the matching
    src/tools/<app>/ backend running in this same process (main.py mounts
    each one at /<app>/v1, see api/v1/router.py under it). Defaults to
    family_aims for an unmapped slug, matching this function's original,
    family_aims-only behavior."""
    host = os.getenv("HOST", "127.0.0.1")
    port = os.getenv("PORT", "8010")
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    app = _TOOLS_APP_BY_SLUG.get(slug, "family_aims")
    return f"http://{probe_host}:{port}/{app}/v1"


_agent_cache: dict[str, tuple[RuntimeArtifact, object]] = {}
_agent_cache_lock = threading.Lock()


def _build_compiled_agent(slug: str) -> tuple[RuntimeArtifact, object]:
    """Actually build the graph for `compiled_agents/<slug>/graph.json` —
    no caching. Use `load_compiled_agent`/`reload_compiled_agent` instead.

    Persistence: `DATABASE_URL` set -> a shared `PostgresSaver` (survives a
    redeploy, unlike local disk on e.g. Render's free tier). Otherwise
    falls back to a SqliteSaver file per agent (`compiled_agents/<slug>/
    sessions.db`) -- fine for local dev, lost on process restart in a real
    deploy. A single shared connection for the process lifetime either way.
    """
    agent_dir = COMPILED_AGENTS_DIR / slug
    graph_path = agent_dir / "graph.json"
    if not graph_path.exists():
        raise FileNotFoundError(f"No compiled agent at {graph_path} — expected graph.json from agent_compiler.")

    artifact = runtime_artifact_from_dict(json.loads(graph_path.read_text(encoding="utf-8")))

    if postgres_enabled():
        checkpointer = build_postgres_checkpointer()
    else:
        db_path = agent_dir / "sessions.db"
        conn = sqlite3.connect(str(db_path), check_same_thread=False)
        checkpointer = SqliteSaver(conn)

    llm_client = OpenAILLMClient(api_key=os.environ.get("OPENAI_API_KEY"))

    graph = build_graph(
        artifact,
        llm_client,
        tools_base_url=_tools_base_url(slug),
        checkpointer=checkpointer,
        say_callback=_say_callback,
    )
    return artifact, graph


def load_compiled_agent(slug: str) -> tuple[RuntimeArtifact, object]:
    """Build (once per slug, cached until `reload_compiled_agent` is called)
    the graph for `compiled_agents/<slug>/graph.json`.

    Returns (artifact, graph) — `artifact` carries the agent_id/session
    timeout metadata `session_resolver.resolve_session` needs; `graph` is
    the compiled, checkpointed LangGraph graph, ready for `.invoke()`.
    """
    with _agent_cache_lock:
        cached = _agent_cache.get(slug)
    if cached is not None:
        return cached

    built = _build_compiled_agent(slug)
    with _agent_cache_lock:
        _agent_cache.setdefault(slug, built)
        return _agent_cache[slug]


def reload_compiled_agent(slug: str) -> RuntimeArtifact:
    """Rebuild `slug` from the `graph.json` currently on disk and swap it
    into the cache, without restarting the process (agent_runtime's
    USAGE.md §9/§8.2 `/admin/reload` pattern). Only this one slug is
    affected -- every other cached agent (and its checkpointer connection)
    is left untouched.

    Safe to call while requests for this slug are in flight: readers hold
    the lock only long enough to read/swap the tuple, `graph.invoke()`
    itself never touches `_agent_cache`, so an in-progress turn keeps
    using the (artifact, graph) pair it already fetched.
    """
    artifact, graph = _build_compiled_agent(slug)
    with _agent_cache_lock:
        _agent_cache[slug] = (artifact, graph)
    return artifact
