"""Test-only HTTP endpoint for a compiled agent_compiler agent, served via
agent_runtime. Pilot for the agent_compiler -> agent_runtime -> agent_executer
pipeline (2026-09-16) — plain request/response, no GHL semantics. GHL
webhook wiring is a separate, later step.

Mounted in main.py at /compiled/{slug}/v1.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from langgraph.types import Command
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from engine.runtime.graph_builder import fresh_state
from engine.runtime.session_resolver import resolve_session

from compiled_runner.loader import load_compiled_agent, message_buffer, reload_compiled_agent

logger = logging.getLogger(__name__)

router = APIRouter()


class CompiledChatRequest(BaseModel):
    thread_id: str
    message: str | None = None
    contact: dict | None = None
    """CRM contact fields for a brand-new session (e.g. name/email/phone/
    language) -- resolves any `{{contact.X}}` reference in the compiled
    graph. Optional and ignored once a session already exists (its contact
    is fixed at creation, same as its other session-scoped state). Real GHL
    wiring is still a later step (see module docstring); this just lets a
    caller supply it directly until then."""


class CompiledChatResponse(BaseModel):
    messages: list[str]
    conversation_ended: bool
    current_state: str | None = None


@router.post("/{slug}/v1/chat", response_model=CompiledChatResponse)
async def chat(slug: str, req: CompiledChatRequest) -> CompiledChatResponse:
    try:
        artifact, graph = load_compiled_agent(slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    config = {"configurable": {"thread_id": req.thread_id}}
    buffer: list[str] = []
    message_buffer.set(buffer)

    snapshot = graph.get_state(config)
    resolution = resolve_session(snapshot.values, artifact.session_timeout_minutes)
    if resolution == "continuing" and not snapshot.next:
        # A thread that already reached a terminal node (snapshot.next == ())
        # has nothing pending to resume -- resolve_session only knows elapsed
        # time, not this. Treat a new message as a fresh conversation.
        resolution = "new"
    logger.info("[%s] thread=%s session=%s", slug, req.thread_id, resolution)

    # graph.invoke() is synchronous and blocking; a tool call this graph
    # makes can hit this same server over HTTP, so it must run off the
    # event loop or that self-call deadlocks against the frozen loop
    # until the tool's own timeout kills it.
    if resolution in ("new", "stale"):
        result = await run_in_threadpool(graph.invoke, fresh_state(artifact, contact=req.contact), config)
    else:
        if not req.message:
            raise HTTPException(status_code=422, detail="message is required to continue an existing session.")
        result = await run_in_threadpool(graph.invoke, Command(resume=req.message), config)

    interrupts = result.get("__interrupt__")
    if interrupts:
        buffer.extend(interrupts[0].value.get("prompt", []))
        conversation_ended = False
    else:
        conversation_ended = True

    final_state = graph.get_state(config).values
    return CompiledChatResponse(
        messages=buffer,
        conversation_ended=conversation_ended,
        current_state=final_state.get("current_state"),
    )


@router.post("/{slug}/v1/reset")
async def reset(slug: str, thread_id: str) -> dict:
    """Test-only convenience: wipe one thread's checkpoint so the next
    /chat call starts a brand-new session. Not part of the compiled
    agent's own contract."""
    _, graph = load_compiled_agent(slug)
    config = {"configurable": {"thread_id": thread_id}}
    graph.checkpointer.delete_thread(thread_id)
    return {"reset": True, "thread_id": thread_id}


@router.post("/{slug}/v1/admin/reload")
async def reload_agent(slug: str) -> dict:
    """Rebuild `slug` from the `graph.json` currently on disk, without
    restarting the process -- agent_runtime's USAGE.md §9/§8.2
    `/admin/reload` pattern. Call this after dropping a new compiled
    `graph.json`/`assets.json` into compiled_agents/<slug>/ (e.g. via
    scripts/sync_compiled_agents.py). Applies to every mount that reads
    this agent through compiled_runner.loader, GHL webhook included --
    slug is the shared cache key, not something specific to this router.
    """
    try:
        artifact = await run_in_threadpool(reload_compiled_agent, slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"reloaded": True, "agent_id": artifact.agent_id}
