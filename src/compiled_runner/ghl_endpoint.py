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

from engine.runtime.graph_builder import fresh_state
from engine.runtime.session_resolver import resolve_session
from instances.helpers.ghl import GhlClientConfig, send_ghl_message_async
from instances.helpers.ghl_request import GhlAgentRequest
from instances.helpers.runner import derive_execution_id
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
    """Normalized (no spaces/dashes/parens) E.164 numbers this pilot accepts.
    Ignored entirely when `enforce_phone_whitelist` is False."""
    debounce_seconds: float
    ghl_client_config: Callable[[], GhlClientConfig]
    """Returns the GhlClientConfig to send replies with -- typically reuses
    an existing agent.json's ghl/runtime/messaging sections (base_url,
    token env var, channel map -- none of that is agent-specific)."""
    enforce_phone_whitelist: bool = True
    """Kill switch for the pilot gate, per agent -- False lets every phone
    through regardless of `allowed_phones` (e.g. once an agent is cleared
    to go live). Defaults to True so an agent stays gated unless a caller
    explicitly opts out; see each agent's own text.py for the .env var
    that drives this (load_phone_whitelist's own docstring)."""
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


# Manual-testing reset marker (2026-09-29, pedido directamente): the classic
# /sam agent had its own version of this ("</>", see agents/helpers/history.py
# ::DEFAULT_RESET_MARKER), but it's not going to be used going forward and,
# separately, never actually matched anyway -- a real GHL/WhatsApp payload
# sends "<\>" (one literal backslash), confirmed live against the exact
# payload shared this session, not "</>" (forward slash). sam_text doesn't
# share any code with that agent (never calls agents.helpers.runner or
# apply_reset_marker -- its "memory" is the LangGraph checkpoint, not
# re-fetched GHL history), so this is its own, independent mechanism.
# Three spellings accepted, so a tester doesn't have to remember which one
# actually reaches the LLM unmangled: "<\>" (what a real payload sends),
# "<\\>" (two backslashes -- what typing the JSON-escaped form seen in a
# logged payload literally into WhatsApp produces), and "</>" (forward
# slash, kept for muscle memory from the classic agent's own marker).
_RESET_MARKERS = ("<\\>", "<\\\\>", "</>")


def _strip_reset_marker(message: str) -> tuple[str, bool]:
    """If `message` starts with a reset marker (either slash direction,
    see _RESET_MARKERS), returns (message with the marker removed, True).
    Otherwise returns (message unchanged, False). Whitespace around the
    marker is ignored on both sides."""
    stripped = message.lstrip()
    for marker in _RESET_MARKERS:
        if stripped.startswith(marker):
            return stripped[len(marker):].strip(), True
    return message, False


GHL_TEXT_HISTORY_MAX_DAYS = float(os.getenv("GHL_TEXT_HISTORY_MAX_DAYS", "7"))
"""Shared across every text agent wired here (2026-09-29, pedido directamente)
-- unlike debounce_seconds/closing_message_lookback_days, this is deliberately
ONE .env var for all of them, not one per agent. How many days of inactivity
before resolve_session treats the next inbound message as a brand-new
conversation instead of resuming the existing one.

Previously this was each agent's own compiled session_timeout_minutes
(manifest.yaml -- content, requires a recompile to change). This env var is
now the authoritative source for the real GHL webhook pipeline (_run_turn
below); each agent's own artifact.session_timeout_minutes is left as-is in
the DSL and still governs compiled_runner/endpoint.py's generic test-chat
tool, which isn't text-agent-specific and has nothing to do with GHL."""

GHL_TEXT_SESSION_TIMEOUT_MINUTES = GHL_TEXT_HISTORY_MAX_DAYS * 24 * 60


_FALSE_STRINGS = {"0", "false", "no", "off"}


def load_phone_whitelist(
    list_env_var: str, enforce_env_var: str, default_phones: str = ""
) -> tuple[frozenset[str], bool]:
    """Reads one agent's phone pilot gate from two .env vars, instead of a
    hardcoded frozenset in that agent's own text.py -- moved here 2026-09-29
    so the actual numbers (and whether the gate applies at all) can change
    without touching code or redeploying.

    `list_env_var`: comma-separated E.164 numbers, e.g.
        "+573215616921,+573007011593". Spaces around commas are trimmed;
        each number is normalized the same way an inbound request's phone
        is (see _normalize_phone) so formatting differences never cause a
        silent mismatch.
    `enforce_env_var`: "true"/"false" (also accepts 1/0, yes/no, on/off,
        case-insensitive). Defaults to enforcing (True) if unset or
        unrecognized -- an agent stays gated unless a caller explicitly
        opts out, never the other way around by accident. This is the
        switch to flip when an agent is cleared to go live: set it to
        false and every phone gets through, no code change needed.
    `default_phones`: used only if `list_env_var` isn't set in .env at all
        -- lets a caller keep working with zero .env setup, same as
        before this moved out of hardcoded Python.
    """
    raw_phones = os.getenv(list_env_var, default_phones)
    phones = frozenset(_normalize_phone(p) for p in raw_phones.split(",") if p.strip())

    raw_enforce = os.getenv(enforce_env_var, "true").strip().lower()
    enforce = raw_enforce not in _FALSE_STRINGS  # anything unrecognized defaults to enforcing, not open

    return phones, enforce


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
            # If conversation was widget (LIVE_CHAT), send closing message via WhatsApp
            # (2026-09-30, pedido directamente)
            reply_channel = row.get("channel")
            if reply_channel == "LIVE_CHAT":
                reply_channel = "WHATSAPP"

            await send_ghl_message_async(
                contact_id=row["contact_id"],
                message=_closing_message_text(cfg, row.get("preferred_language")),
                channel=reply_channel,
                exec_id=f"{cfg.slug}:closing:{thread_id}",
                config=cfg.ghl_client_config(),
                location_id=row["location_id"],
                logger=logger,
            )
            await run_in_threadpool(mark_closing_message_sent, cfg.slug, row["location_id"], row["contact_id"])
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
        if cfg.enforce_phone_whitelist and phone not in cfg.allowed_phones:
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
        combined_message, reset_requested = _strip_reset_marker(combined_message)
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
            resolution = resolve_session(snapshot.values, GHL_TEXT_SESSION_TIMEOUT_MINUTES)
            if resolution == "continuing" and not snapshot.next:
                # resolve_session only knows elapsed time, not whether the
                # graph actually has a pending interrupt -- a thread that
                # already reached a terminal node (snapshot.next == ()) has
                # nothing to resume, so Command(resume=...) would silently
                # do nothing. Treat this as the start of a fresh conversation.
                resolution = "new"
            if reset_requested:
                resolution = "new"
                logger.info("[%s] Reset marker detected -- forcing a fresh conversation.", execution_id)
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
                # If message came from widget (LIVE_CHAT), respond via WhatsApp
                # (2026-09-30, pedido directamente)
                reply_channel = request.channel
                if reply_channel == "LIVE_CHAT":
                    logger.info(
                        "[%s] Inbound channel was widget (LIVE_CHAT), redirecting reply to WHATSAPP.", execution_id
                    )
                    reply_channel = "WHATSAPP"

                await send_ghl_message_async(
                    contact_id=request.contact_id,
                    message=reply_text,
                    channel=reply_channel,
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
