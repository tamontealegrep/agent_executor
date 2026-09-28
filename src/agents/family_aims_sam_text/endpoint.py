"""GHL webhook endpoint for the compiled family_aims_sam_text agent.

Mirrors agents/family_aims_sam/endpoint.py's contract (202 immediately,
background task, idempotent by execution_id, replies via
send_ghl_message_async) but runs the graph built from
compiled_agents/family_aims_sam_text/graph.json (agent_compiler +
agent_runtime, LangGraph) instead of the free-form LangChain tool loop.

"Memory" here is the graph's own checkpointer (SqliteSaver, one file per
agent -- see compiled_runner/loader.py): session state is keyed by
thread_id and survives across turns without re-fetching GHL history, so a
brand-new thread only needs to seed the first inbound message
(fresh_state's seed_last_user_message) and every later turn just resumes
the interrupted graph with Command(resume=...).

Debounce (2026-09-28): WhatsApp delivers one webhook per message, so a
contact typing several bubbles in a row (normal WhatsApp pattern) would
otherwise fire one graph execution per bubble, racing on the same
thread_id. Incoming text for a given thread is buffered and the actual
graph run is delayed SAM_TEXT_DEBOUNCE_SECONDS after the *last* message --
each new message resets the timer, so the graph only runs once the
contact has actually stopped typing, with every buffered line joined into
one turn.

Pilot gate (2026-09-28): only messages from ALLOWED_TEST_PHONES are
processed -- this endpoint is still being validated live, so it stays
opt-in to a handful of known test numbers.
"""

import asyncio
import logging
import os
from dataclasses import dataclass, field

from fastapi import APIRouter, status
from langgraph.types import Command
from starlette.concurrency import run_in_threadpool

from agent_compiler.runtime.graph_builder import fresh_state
from agent_compiler.runtime.session_resolver import resolve_session
from agents.family_aims_sam.settings import get_agent_definition
from agents.helpers.ghl import GhlClientConfig, build_ghl_client_config, send_ghl_message_async
from agents.helpers.ghl_request import GhlAgentRequest
from agents.helpers.runner import derive_execution_id
from compiled_runner.loader import load_compiled_agent, message_buffer

logger = logging.getLogger(__name__)
router = APIRouter()

SLUG = "family_aims_sam_text"

ALLOWED_TEST_PHONES = {
    "+573215616921",
    "+573007011593",
    "+573016804227",
    "+573103725324",
}

DEBOUNCE_SECONDS = float(os.getenv("SAM_TEXT_DEBOUNCE_SECONDS", "15"))

processing_messages: set[str] = set()


class SamTextRequest(GhlAgentRequest):
    pass


@dataclass
class _PendingTurn:
    """Text accumulated for one thread_id, waiting out the debounce window."""

    execution_ids: list[str] = field(default_factory=list)
    message_parts: list[str] = field(default_factory=list)
    last_request: SamTextRequest | None = None
    flush_task: "asyncio.Task | None" = None


# thread_id -> turn currently accumulating. Popped as soon as its debounce
# timer fires, so a message arriving while the graph is already running
# starts a brand-new turn rather than racing the one in flight.
_pending_turns: dict[str, _PendingTurn] = {}


def _normalize_phone(raw: object) -> str:
    return str(raw or "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")


def _ghl_client_config() -> GhlClientConfig:
    agent_settings = get_agent_definition()
    return build_ghl_client_config(
        agent_settings.get("ghl", {}),
        agent_settings.get("runtime", {}),
        agent_settings.get("messaging", {}),
    )


@router.post("/sam_text", status_code=status.HTTP_202_ACCEPTED)
async def sam_text_endpoint(request: SamTextRequest):
    phone = _normalize_phone((request.contact or {}).get("phone"))
    if phone not in ALLOWED_TEST_PHONES:
        logger.info("[sam_text] Ignoring message from non-allowed phone: %s", phone)
        return {"status": "ignored", "reason": "phone_not_allowed"}

    if not request.location_id or not request.contact_id:
        logger.warning("[sam_text] Missing location_id/contact_id, dropping request.")
        return {"status": "ignored", "reason": "missing_ids"}

    execution_id = derive_execution_id(request, prefix="sam_text")
    logger.info(
        "[%s] Received sam_text request: conversation_id=%s contact_id=%s location_id=%s",
        execution_id,
        request.conversation_id,
        request.contact_id,
        request.location_id,
    )

    if execution_id in processing_messages:
        logger.info("[%s] Already processing/processed, skipping.", execution_id)
        return {"status": "duplicate", "execution_id": execution_id}
    processing_messages.add(execution_id)

    thread_id = f"{request.location_id}:{request.contact_id}"
    turn = _pending_turns.get(thread_id)
    if turn is None:
        turn = _PendingTurn()
        _pending_turns[thread_id] = turn
    elif turn.flush_task is not None:
        turn.flush_task.cancel()

    turn.execution_ids.append(execution_id)
    if request.message:
        turn.message_parts.append(str(request.message))
    turn.last_request = request
    turn.flush_task = asyncio.create_task(_debounced_flush(thread_id))

    return {"status": "accepted", "execution_id": execution_id, "debounce_seconds": DEBOUNCE_SECONDS}


async def _debounced_flush(thread_id: str) -> None:
    try:
        await asyncio.sleep(DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return  # a newer message reset the timer -- this flush is stale

    turn = _pending_turns.pop(thread_id, None)
    if turn is None:
        return
    await _run_sam_text_turn(thread_id, turn)


async def _run_sam_text_turn(thread_id: str, turn: _PendingTurn) -> None:
    request = turn.last_request
    combined_message = "\n".join(turn.message_parts).strip()
    execution_id = turn.execution_ids[-1]
    if len(turn.execution_ids) > 1:
        logger.info(
            "[%s] Flushing %s buffered message(s) for thread=%s after %ss of inactivity.",
            execution_id,
            len(turn.execution_ids),
            thread_id,
            DEBOUNCE_SECONDS,
        )

    try:
        config = {"configurable": {"thread_id": thread_id}}
        artifact, graph = load_compiled_agent(SLUG)

        buffer: list[str] = []
        message_buffer.set(buffer)

        snapshot = graph.get_state(config)
        resolution = resolve_session(snapshot.values, artifact.session_timeout_minutes)
        if resolution == "continuing" and not snapshot.next:
            # resolve_session only knows elapsed time, not whether the graph
            # actually has a pending interrupt -- a thread that already
            # reached a terminal node (snapshot.next == ()) has nothing to
            # resume, so Command(resume=...) would silently do nothing.
            # Treat a new message here as the start of a fresh conversation.
            resolution = "new"
        logger.info("[%s] thread=%s session=%s", execution_id, thread_id, resolution)

        # graph.invoke() is synchronous and blocking; a tool call this graph
        # makes (e.g. check_visa) hits this same server over HTTP, so it must
        # run off the event loop or that self-call deadlocks against the
        # frozen loop until the tool's own timeout kills it.
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

        reply_text = "\n\n".join(buffer).strip()
        if reply_text:
            await send_ghl_message_async(
                contact_id=request.contact_id,
                message=reply_text,
                channel=request.channel,
                exec_id=execution_id,
                config=_ghl_client_config(),
                location_id=request.location_id,
                reply_message_id=request.messageId,
                logger=logger,
            )
            logger.info("[%s] Reply sent to GHL.", execution_id)
        else:
            logger.info("[%s] Graph produced no text to send (empty buffer).", execution_id)

    except Exception:
        logger.error("[%s] sam_text background execution failed.", execution_id, exc_info=True)
    finally:
        for eid in turn.execution_ids:
            processing_messages.discard(eid)
