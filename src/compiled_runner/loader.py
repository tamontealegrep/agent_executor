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
from functools import lru_cache
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


def _tools_base_url() -> str:
    """Where this pilot's tool calls land — the family_aims tools already
    running in this same process (main.py mounts them at /family_aims/v1,
    both the original hyphenated routes and the snake_case aliases the
    tool_executor actually calls — see api/v1/router.py)."""
    host = os.getenv("HOST", "127.0.0.1")
    port = os.getenv("PORT", "8010")
    probe_host = "127.0.0.1" if host == "0.0.0.0" else host
    return f"http://{probe_host}:{port}/family_aims/v1"


@lru_cache
def load_compiled_agent(slug: str) -> tuple[RuntimeArtifact, object]:
    """Build (once per slug, cached) the graph for `compiled_agents/<slug>/graph.json`.

    Returns (artifact, graph) — `artifact` carries the agent_id/session
    timeout metadata `session_resolver.resolve_session` needs; `graph` is
    the compiled, checkpointed LangGraph graph, ready for `.invoke()`.

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
        tools_base_url=_tools_base_url(),
        checkpointer=checkpointer,
        say_callback=_say_callback,
    )
    return artifact, graph
