"""Generic GHL webhook -> compiled LangGraph agent -> GHL reply endpoint.

Every compiled agent wired to a real GHL webhook needs the exact same
plumbing (202 immediately, per-thread debounce, session resolution with
the terminal-state fix, run_in_threadpool for the graph.invoke() deadlock,
idempotency by execution_id, reply via send_ghl_message_async, mirror the
turn into `conversations`). Only the agent identity, phone allowlist, and
debounce window differ -- see family_aims_sam_text/endpoint.py's own
docstring for the diagnosis behind each of the fixes baked in here. One
agent's endpoint module is now just a `CompiledAgentGhlConfig` plus a call
to `build_compiled_agent_router` instead of duplicating this file.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable

from fastapi import APIRouter, status
from langgraph.types import Command
from starlette.concurrency import run_in_threadpool

from agent_compiler.runtime.graph_builder import fresh_state
from agent_compiler.runtime.session_resolver import resolve_session
from agents.helpers.ghl import GhlClientConfig, send_ghl_message_async
from agents.helpers.ghl_request import GhlAgentRequest
from agents.helpers.runner import derive_execution_id
from compiled_runner.loader import load_compiled_agent, message_buffer
from compiled_runner.postgres import (
    find_conversations_needing_closing_message,
    mark_closing_message_sent,
    postgres_enabled,
    upsert_conversation,
)


@dataclass(frozen=True)
class CompiledAgentGhlConfig:
    """Everything that differs from one compiled agent's GHL endpoint to another."""

    slug: str
    """Must match a folder under compiled_agents/."""
    path: str
    """Route path, e.g. "/sam_text" -> mounted as POST {path}."""
    allowed_phones: frozenset[str]
    """Normalized (no spaces/dashes/parens) E.164 numbers this pilot accepts."""
    debounce_seconds: float
    ghl_client_config: Callable[[], GhlClientConfig]
    """Returns the GhlClientConfig to send replies with -- typically reuses
    an existing agent.json's ghl/runtime/messaging sections (base_url,
    token env var, channel map -- none of that is agent-specific)."""
    closing_message_lookback_days: float = 3.0
    """How many days back `run_closing_sweep_once` still considers a
    candidate -- a safety bound, not the trigger itself (that's always
    ~23h45m of inactivity, see postgres.CLOSING_MESSAGE_INACTIVITY_THRESHOLD,
    a WhatsApp platform constant, not a per-agent choice). Guards against a
    sweep that was down for a while suddenly messaging contacts from
    long-dead conversations once it catches back up. One .env var per
    agent, same pattern as debounce_seconds -- see each agent's own
    text.py."""
    closing_message: "str | dict[str, str] | None" = None
    """Override for the text sent by the closing sweep. A plain string
    always wins; a dict is looked up by the thread's preferred_language
    (falls back to the built-in ES/EN/PT default). None uses the built-in
    default entirely -- deliberately generic ("puede ser de cualquiera"),
    since the sweep's only job is to get *something* out before Meta's 24h
    window closes, not to carry flow-specific content."""


_DEFAULT_CLOSING_MESSAGES: dict[str, str] = {
    "es": "Gracias por contactarte con nosotros. Si tienes alguna otra pregunta, no dudes en escribirnos. ¡Que tengas un buen día!",
    "en": "Thank you for reaching out to us. If you have any other questions, feel free to write to us anytime. Have a great day!",
    "pt": "Obrigado por entrar em contato conosco. Se tiver mais alguma pergunta, não hesite em nos escrever. Tenha um ótimo dia!",
}


def _closing_message_text(cfg: CompiledAgentGhlConfig, preferred_language: "str | None") -> str:
    if isinstance(cfg.closing_message, str):
        return cfg.closing_message
    messages = cfg.closing_message if isinstance(cfg.closing_message, dict) else _DEFAULT_CLOSING_MESSAGES
    lang = (preferred_language or "es").lower()
    return messages.get(lang) or messages.get("es") or next(iter(messages.values()))


def _normalize_phone(raw: object) -> str:
    return str(raw or "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")


@dataclass
class _PendingTurn:
    """Text accumulated for one thread_id, waiting out the debounce window."""

    execution_ids: list[str] = field(default_factory=list)
    message_parts: list[str] = field(default_factory=list)
    last_request: object = None
    flush_task: "asyncio.Task | None" = None


async def run_closing_sweep_once(cfg: CompiledAgentGhlConfig) -> int:
    """One pass: find every thread of `cfg.slug` sitting at 23:45+ of
    inactivity with no closing message sent yet for this window, and send
    one. Module-level (not a closure inside build_compiled_agent_router)
    so it's directly testable/callable without spinning up the router.

    Deliberately bypasses the compiled graph entirely -- there's no new
    user message to run it against, and the message is intentionally
    generic (see CompiledAgentGhlConfig.closing_message's own docstring),
    so a direct send_ghl_message_async call is simpler and correct.

    Returns how many closing messages were actually sent (tests read this;
    production code doesn't need to).
    """
    logger = logging.getLogger(f"compiled_runner.ghl_endpoint.{cfg.slug}")
    candidates = await run_in_threadpool(
        find_conversations_needing_closing_message, cfg.slug, cfg.closing_message_lookback_days
    )
    sent = 0
    for row in candidates:
        thread_id = row["thread_id"]
        try:
            await send_ghl_message_async(
                contact_id=row["contact_id"],
                message=_closing_message_text(cfg, row.get("preferred_language")),
                channel=row.get("channel"),
                exec_id=f"{cfg.slug}:closing:{thread_id}",
                config=cfg.ghl_client_config(),
                location_id=row["location_id"],
                logger=logger,
            )
            await run_in_threadpool(mark_closing_message_sent, thread_id)
            logger.info("[%s] Closing message sent after ~24h of inactivity.", thread_id)
            sent += 1
        except Exception:
            logger.error("[%s] Failed to send closing message.", thread_id, exc_info=True)
    return sent


async def _closing_sweep_loop(cfg: CompiledAgentGhlConfig) -> None:
    logger = logging.getLogger(f"compiled_runner.ghl_endpoint.{cfg.slug}")
    interval_seconds = float(os.getenv("CLOSING_MESSAGE_SWEEP_INTERVAL_SECONDS", "300"))
    while True:
        try:
            await run_closing_sweep_once(cfg)
        except Exception:
            logger.error("Closing-message sweep iteration failed.", exc_info=True)
        await asyncio.sleep(interval_seconds)


def build_compiled_agent_router(cfg: CompiledAgentGhlConfig) -> APIRouter:
    """Build a self-contained router for one compiled agent's GHL webhook.

    State (processing_messages, pending turns) is local to this call, so
    two agents built from this factory never share or race on each
    other's in-flight messages, even though the shapes are identical.
    """
    router = APIRouter()
    logger = logging.getLogger(f"compiled_runner.ghl_endpoint.{cfg.slug}")

    processing_messages: set[str] = set()
    pending_turns: dict[str, _PendingTurn] = {}

    # Not a locally-scoped subclass: with `from __future__ import
    # annotations`, FastAPI resolves a parameter's type hint by name from
    # the function's __globals__ at route-registration time -- a class
    # defined inside this factory function isn't reachable that way, so
    # `request` silently fell back to being treated as a query param
    # instead of the JSON body (found live: every call 422'd on "request
    # missing"). GhlAgentRequest is already generic across agents, so
    # there's nothing a subclass would add here anyway.
    @router.post(cfg.path, status_code=status.HTTP_202_ACCEPTED)
    async def endpoint(request: GhlAgentRequest):
        phone = _normalize_phone((request.contact or {}).get("phone"))
        if phone not in cfg.allowed_phones:
            logger.info("Ignoring message from non-allowed phone: %s", phone)
            return {"status": "ignored", "reason": "phone_not_allowed"}

        if not request.location_id or not request.contact_id:
            logger.warning("Missing location_id/contact_id, dropping request.")
            return {"status": "ignored", "reason": "missing_ids"}

        execution_id = derive_execution_id(request, prefix=cfg.slug)
        logger.info(
            "[%s] Received request: conversation_id=%s contact_id=%s location_id=%s",
            execution_id,
            request.conversation_id,
            request.contact_id,
            request.location_id,
        )

        if execution_id in processing_messages:
            logger.info("[%s] Already processing/processed, skipping.", execution_id)
            return {"status": "duplicate", "execution_id": execution_id}
        processing_messages.add(execution_id)

        # Prefixed with the agent slug: makes "which agent is this" visible
        # at a glance in a shared Postgres, and keeps two different agents
        # talking to the same contact from colliding on the exact same
        # thread_id and corrupting each other's state.
        thread_id = f"{cfg.slug}:{request.location_id}:{request.contact_id}"
        turn = pending_turns.get(thread_id)
        if turn is None:
            turn = _PendingTurn()
            pending_turns[thread_id] = turn
        elif turn.flush_task is not None:
            turn.flush_task.cancel()

        turn.execution_ids.append(execution_id)
        if request.message:
            turn.message_parts.append(str(request.message))
        turn.last_request = request
        turn.flush_task = asyncio.create_task(_debounced_flush(thread_id))

        return {"status": "accepted", "execution_id": execution_id, "debounce_seconds": cfg.debounce_seconds}

    async def _debounced_flush(thread_id: str) -> None:
        try:
            await asyncio.sleep(cfg.debounce_seconds)
        except asyncio.CancelledError:
            return  # a newer message reset the timer -- this flush is stale

        turn = pending_turns.pop(thread_id, None)
        if turn is None:
            return
        await _run_turn(thread_id, turn)

    async def _run_turn(thread_id: str, turn: _PendingTurn) -> None:
        request = turn.last_request
        combined_message = "\n".join(turn.message_parts).strip()
        execution_id = turn.execution_ids[-1]
        if len(turn.execution_ids) > 1:
            logger.info(
                "[%s] Flushing %s buffered message(s) for thread=%s after %ss of inactivity.",
                execution_id,
                len(turn.execution_ids),
                thread_id,
                cfg.debounce_seconds,
            )

        try:
            config = {"configurable": {"thread_id": thread_id}}
            artifact, graph = load_compiled_agent(cfg.slug)

            buffer: list[str] = []
            message_buffer.set(buffer)

            snapshot = graph.get_state(config)
            resolution = resolve_session(snapshot.values, artifact.session_timeout_minutes)
            if resolution == "continuing" and not snapshot.next:
                # resolve_session only knows elapsed time, not whether the
                # graph actually has a pending interrupt -- a thread that
                # already reached a terminal node (snapshot.next == ()) has
                # nothing to resume, so Command(resume=...) would silently
                # do nothing. Treat this as the start of a fresh conversation.
                resolution = "new"
            logger.info("[%s] thread=%s session=%s", execution_id, thread_id, resolution)

            # graph.invoke() is synchronous and blocking; a tool call this
            # graph makes (e.g. check_visa) hits this same server over HTTP,
            # so it must run off the event loop or that self-call deadlocks
            # against the frozen loop until the tool's own timeout kills it.
            if resolution in ("new", "stale"):
                state = fresh_state(
                    artifact,
                    contact=request.contact,
                    seed_history=[f"user: {combined_message}"] if combined_message else [],
                    seed_last_user_message=combined_message,
                )
                result = await run_in_threadpool(graph.invoke, state, config)
            else:
                if not combined_message:
                    raise ValueError("message is required to continue an existing session.")
                result = await run_in_threadpool(graph.invoke, Command(resume=combined_message), config)

            interrupts = result.get("__interrupt__")
            if interrupts:
                buffer.extend(interrupts[0].value.get("prompt", []))

            final_state = graph.get_state(config).values
            upsert_conversation(
                thread_id=thread_id,
                agent_slug=cfg.slug,
                location_id=request.location_id,
                contact_id=request.contact_id,
                contact=final_state.get("contact"),
                slots=final_state.get("slots"),
                current_state=final_state.get("current_state"),
                last_user_message=final_state.get("last_user_message"),
                history=final_state.get("history"),
                channel=request.channel,
            )

            reply_text = "\n\n".join(buffer).strip()
            if reply_text:
                await send_ghl_message_async(
                    contact_id=request.contact_id,
                    message=reply_text,
                    channel=request.channel,
                    exec_id=execution_id,
                    config=cfg.ghl_client_config(),
                    location_id=request.location_id,
                    reply_message_id=request.messageId,
                    logger=logger,
                )
                logger.info("[%s] Reply sent to GHL.", execution_id)
            else:
                logger.info("[%s] Graph produced no text to send (empty buffer).", execution_id)

        except Exception:
            logger.error("[%s] Background execution failed.", execution_id, exc_info=True)
        finally:
            for eid in turn.execution_ids:
                processing_messages.discard(eid)

    @router.on_event("startup")
    async def _start_closing_sweep() -> None:
        if postgres_enabled():
            asyncio.create_task(_closing_sweep_loop(cfg))

    return router
