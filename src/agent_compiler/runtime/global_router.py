"""Global router — the interrupt-loop mechanism (DESIGN_PATTERNS.md P04).

A "global router" (checking handler triggers / FAQ matches before treating
a reply as an answer) needs to intercept every inbound message — but in a
compiled graph, a "turn" only exists at the exact point a paused
`interrupt()` resumes. `graph_builder.py` wraps a question node's
`interrupt()` call in a loop; this module supplies the two decisions that
loop needs on each reply: whether it matches anything, and if so, where
handling it leads.

Decoupled from `graph_builder.py`'s condition-evaluation internals via
dependency injection (`resolve_edges`, `on_say`) rather than an import —
keeps this module trivially testable with fakes and avoids a circular
import between the two.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from agent_compiler.dsl.utils import is_dynamic_target
from agent_compiler.runtime.llm_client import LLMClient, LLMContext
from agent_compiler.targets.langgraph.graph_renderer import (
    FaqNode,
    GlobalRouterDefinition,
    GraphEdge,
    GraphNode,
)

RouterMatch = tuple[str, "GraphNode | FaqNode"]
"""`(kind, node)` where `kind` is `"handler"` or `"faq"`."""


def _candidates(router: GlobalRouterDefinition) -> list[RouterMatch]:
    return [("handler", h) for h in router.handlers] + [("faq", f) for f in router.faqs]


def _candidate_lines(candidates: list[RouterMatch]) -> str:
    lines = []
    for i, (kind, node) in enumerate(candidates):
        phrases = node.trigger if kind == "handler" else node.match
        node_id = node.node_id if kind == "handler" else node.faq_id
        lines.append(f"{i}. [{kind}:{node_id}] triggers on: {phrases}")
    return "\n".join(lines)


def _continues_the_flow(
    llm_client: LLMClient,
    ctx: LLMContext,
    candidates: list[RouterMatch],
    candidate_lines: str,
) -> bool:
    """Stage 1: Determine if the user message is a normal continuation.

    This stage decides if the reply is a plausible answer to the pending question
    or a simple acknowledgement, skipping the interrupt matching process for the
    common case of a normal flow.

    Parameters:
        llm_client: Client used to execute completion prompts.
        ctx: The current conversation context.
        candidates: List of all possible global interrupts.
        candidate_lines: Formatted string of candidate descriptions for the prompt.

    Returns:
        True if the message is a continuation, False if it might be an interrupt.
    """
    # No deterministic pre-check: always consult the LLM for Stage 1.

    question_context = f"The agent just asked: {ctx.pending_question!r}\n\n" if ctx.pending_question else ""
    prompt = (
        "You are a conversation flow analyzer. An agent is interacting with a user. "
        "Your task is to decide if the user's latest reply is a normal CONTINUATION "
        "of the current topic or if it represents an INTERRUPT that matches one of the "
        "global triggers listed below.\n\n"
        f"{ctx.block()}"
        f"{question_context}"
        f"Global triggers (interrupts):\n{candidate_lines}\n\n"
        "GUIDELINES:\n"
        "1. A CONTINUATION is any reply that directly answers the pending question, "
        "provides relevant information for the current topic, or is a simple "
        "acknowledgement (e.g., \"ok\", \"fine\", \"thanks\", \"hello\").\n"
        "2. An INTERRUPT is a clear shift to a different topic, a request for "
        "information not asked for, a correction of a previous choice (e.g., \"I "
        "changed my mind\", \"sorry I meant...\"), or an explicit match to one "
        "of the triggers listed.\n"
        "3. If the user's reply is ambiguous but could reasonably be an answer to "
        "the pending question, prefer CONTINUE.\n\n"
        "Reply with just CONTINUE or INTERRUPT."
    )
    return llm_client.complete(prompt).strip().upper() != "INTERRUPT"


def _match_candidate(
    llm_client: LLMClient,
    candidates: list[RouterMatch],
    ctx: LLMContext,
    candidate_lines: str,
) -> RouterMatch | None:
    """Stage 2: Identify which specific handler or FAQ matches the interrupt.

    This stage is only called if Stage 1 decided the message is not a
    continuation. It selects the best match among the provided candidates.

    Parameters:
        llm_client: Client used to execute completion prompts.
        candidates: List of all possible global interrupts.
        ctx: The current conversation context.
        candidate_lines: Formatted string of candidate descriptions for the prompt.

    Returns:
        The matched (kind, node) tuple, or None if no candidate matches.
    """
    # Do not short-circuit deterministically; rely on the LLM for selection.

    question_context = f"The agent just asked: {ctx.pending_question!r}\n\n" if ctx.pending_question else ""
    prompt = (
        "The user's reply below has been judged as an interrupt. Which specific "
        "global interrupt (handler or FAQ) does it match best?\n\n"
        f"{ctx.block()}"
        f"{question_context}"
        f"Candidates:\n{candidate_lines}\n\n"
        "Select the candidate that best captures the user's intent. If the reply is "
        "a correction of a previous choice, match it to the trigger that represents "
        "the newly requested topic. If none of the candidates are a clear and "
        "unambiguous match, reply NONE.\n\n"
        "Reply with just the number of the matching candidate, or NONE."
    )
    text = llm_client.complete(prompt)
    if text.isdigit() and int(text) < len(candidates):
        return candidates[int(text)]
    return None


def match_global_router(
    llm_client: LLMClient,
    router: GlobalRouterDefinition,
    ctx: LLMContext,
) -> RouterMatch | None:
    """Check if the user message matches a global handler or FAQ.

    Evaluates the message in two stages: first determining if it's a flow
    continuation, then matching it to a specific interrupt if not.

    Parameters:
        llm_client: Client used to execute completion prompts.
        router: The definition of global handlers and FAQs.
        ctx: The current conversation context.

    Returns:
        The matched (kind, node) tuple if an interrupt is found, else None.
    """
    candidates = _candidates(router)
    if not candidates:
        return None

    lines = _candidate_lines(candidates)
    if _continues_the_flow(llm_client, ctx, candidates, lines):
        return None
    return _match_candidate(llm_client, candidates, ctx, lines)


def handle_global_router_match(
    match: RouterMatch,
    ctx: LLMContext,
    *,
    resolve_edges: Callable[[list[GraphEdge], LLMContext], str | None],
    on_say: Callable[[GraphNode | FaqNode, list[str]], None] | None = None,
) -> str | None:
    """Resolve the destination of a matched handler or FAQ.

    Executes the matched node's logic, including rendering its message and
    resolving its routing edges to determine the next state.

    Parameters:
        match: The (kind, node) pair from match_global_router.
        ctx: The current conversation context.
        resolve_edges: Callback to evaluate routing conditions.
        on_say: Optional callback to handle the node's output message.

    Returns:
        The ID of the next state to jump to, or None to resume the previous flow.
    """
    kind, node = match
    if node.say and on_say is not None:
        on_say(node, node.say)

    if kind == "faq":
        resume_to = node.resume_to
        if resume_to and not is_dynamic_target(resume_to):
            return resume_to
        return None

    handler_node = node  # GraphNode
    handler_ctx = replace(ctx, goal=handler_node.goal, do=handler_node.do)
    target = resolve_edges(handler_node.route, handler_ctx)
    if target is None:
        target = resolve_edges(handler_node.fallback, handler_ctx)
    if target is None or is_dynamic_target(target):
        return None
    return target


UNANSWERED_QUESTION_ACK: list[str] = [
    "Buena pregunta — eso te lo puede aclarar mejor tu asesor en la charla."
]
"""Fixed, non-specific deflection said when `has_unanswered_side_question`
fires (2026-09-21). Deliberately generic and fact-free — never mentions
anything about the business, so it carries zero fabrication risk. Authored
in Spanish and left to `render_say`'s own translate-via-rephrase (already
how the rest of this agent's `say`/FAQ content reaches other languages),
not per-language here."""


def has_unanswered_side_question(llm_client: LLMClient, ctx: LLMContext) -> bool:
    """Check if the reply contains a side question not covered by FAQs or handlers.

    Determines if the user's message contains a fact-seeking request that is
    not handled by existing logic, allowing the agent to provide a generic
    deflection rather than ignoring it.

    Parameters:
        llm_client: Client used to execute completion prompts.
        ctx: The current conversation context.

    Returns:
        True if an unanswered side question is detected, else False.
    """
    policy_block = f"House rules:\n{chr(10).join(ctx.policies)}\n\n" if ctx.policies else ""
    prompt = (
        "A conversational agent just received the user's latest reply below. It has "
        "already been checked against every specific FAQ and handler this agent has, "
        "and none matched — it will be captured normally as an answer to the pending "
        "question.\n\n"
        f"{ctx.block()}"
        f"{policy_block}"
        "Does this reply ALSO contain a genuine question or request for specific "
        "information (a fact, a number, a procedure detail, a personal-situation "
        "judgment) that is not already covered by the conversation so far, and that "
        "this agent has no specific approved content to answer? A bare answer to the "
        "pending question, a greeting, or a short acknowledgement (\"ok\", \"yes\",\"sí\", "
        "\"thanks\", \"thank you\", \"gracias\") is NOT this case.\n\n"
        "Reply with just YES or NO."
    )
    return llm_client.complete(prompt).strip().upper().startswith("Y")
