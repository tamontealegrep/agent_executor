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

from engine.dsl.utils import is_dynamic_target
from engine.runtime.llm_client import LLMClient, LLMContext
from engine.targets.langgraph.graph_renderer import (
    FaqNode,
    GlobalRouterDefinition,
    GraphEdge,
    GraphNode,
)

RouterMatch = tuple[str, "GraphNode | FaqNode"]
"""`(kind, node)` where `kind` is `"handler"` or `"faq"`."""


# Acceptable FAQ ids to source firewall content from the agent spec.
FIREWALL_FAQ_IDS: tuple[str, ...] = ("FAQ_FIREWALL", "F_FIREWALL", "FAQ__FIREWALL", "F__FIREWALL")


def find_firewall_faq(router: GlobalRouterDefinition) -> FaqNode | None:
    for faq in router.faqs:
        if getattr(faq, "faq_id", None) in FIREWALL_FAQ_IDS:
            return faq
    return None


# Acceptable Handler ids to source firewall behavior from the agent spec.
FIREWALL_HANDLER_IDS: tuple[str, ...] = ("HANDLER_FIREWALL", "H_FIREWALL", "HANDLER__FIREWALL", "H__FIREWALL")


def find_firewall_handler(router: GlobalRouterDefinition) -> GraphNode | None:
    for handler in router.handlers:
        if getattr(handler, "node_id", None) in FIREWALL_HANDLER_IDS:
            return handler
    return None


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
    candidate_lines: str,
) -> bool:
    """Stage 1: does `user_reply` continue the pending question normally?

    A cheap, narrow judgment call, deliberately separate from candidate
    matching (2026-09-17, requested directly — repeated router misfires
    prompted asking whether the FAQ/handler mechanism needed a single
    generic redesign; this two-stage split, with real conversation history
    instead of just the one pending question, is that redesign, done in
    place rather than as a parallel system). Recent history matters here in
    a way a single `pending_question` string can't capture — a reply can
    continue a *topic* the conversation has been on for several turns even
    when it doesn't read as a direct answer to the literal last question
    asked (e.g. still discussing a FAQ topic raised a couple of turns back).
    When this returns `True`, the caller skips candidate matching (Stage 2,
    `_match_candidate`) entirely — one call, not two, for the common case.

    Still shown the actual candidate list, even though this stage never
    picks one: dropping it (an earlier version of this function did, to
    keep the call "cheap") measurably hurt recall on real content — a reply
    that is *itself* one of the trigger phrases verbatim (e.g. `"visa
    colombia"`, `F_VISA`'s own trigger) needs to be compared against real
    candidates to be recognized as an interrupt at all; judged in the
    abstract, without seeing what interrupts even exist, it dropped from
    3/3 to 7/10 live. The saving this stage exists for is the *second*
    call, not this one skipping context.
    """
    question_context = f"The agent just asked: {ctx.pending_question!r}\n\n" if ctx.pending_question else ""
    prompt = (
        "A conversational agent asked the user a question. Does the user's latest "
        "reply continue answering it normally, in the context of the recent "
        "conversation — or does it match one of the global interrupts listed below "
        "(things the agent should handle regardless of what it just asked)?\n\n"
        f"{ctx.block()}"
        f"{question_context}"
        f"Global interrupts:\n{candidate_lines}\n\n"
        "A reply that directly and plausibly answers the pending question is always "
        "a continuation, even if it also loosely resembles one of the interrupts "
        "above (e.g. stating a specific nationality answers a nationality question, "
        "it does not raise a foreign-residency FAQ). A bare one- or two-word reply "
        "(a lone \"sí\"/\"no\"/\"ok\"/\"claro\") is almost always a continuation too — a "
        "genuine interrupt takes more than a couple of words to express, unless it "
        "IS itself, word for word, one of the trigger phrases listed.\n\n"
        "Reply with just CONTINUE, or INTERRUPT if it clearly matches one of the "
        "interrupts above."
    )
    return llm_client.complete(prompt).strip().upper() != "INTERRUPT"


def should_block_message(llm_client: LLMClient, ctx: LLMContext) -> bool:
    """Top-level firewall: decide whether the agent should refuse to answer.

    Runs before evaluating handlers or FAQs. Returns True to BLOCK when the
    user's last message clearly asks for something out of scope, disallowed by
    house rules, or unsafe to answer (medical/legal/financial diagnosis or
    advice, personal data collection, explicit content, or operational actions
    the agent cannot perform). Otherwise returns False to ALLOW normal flow.

    The refusal copy is owned by the agent via a dedicated Handler or FAQ.
    """
    policy_block = f"House rules (non-exhaustive):\n{chr(10).join(ctx.policies)}\n\n" if ctx.policies else ""
    question_context = (
        f"The agent just asked the user: {ctx.pending_question!r}\n\n" if ctx.pending_question else ""
    )
    prompt = (
        "You are a gatekeeper for a production assistant. Decide if the assistant "
        "is allowed to respond SUBSTANTIVELY to the user's latest message below. "
        "BLOCK when the request is clearly outside the assistant's domain, asks for "
        "prohibited or unsafe content (medical/legal/financial advice or diagnosis, "
        "explicit content, sensitive personal data collection), or requests actions "
        "the assistant cannot perform per house rules. Otherwise ALLOW.\n\n"
        f"{ctx.block()}"
        f"{question_context}"
        f"{policy_block}"
        "Reply with just one word: ALLOW or BLOCK."
    )
    decision = llm_client.complete(prompt).strip().upper()
    return decision == "BLOCK"


def _match_candidate(
    llm_client: LLMClient,
    candidates: list[RouterMatch],
    ctx: LLMContext,
    candidate_lines: str,
) -> RouterMatch | None:
    """Stage 2: which handler/FAQ, if any, does `user_reply` actually match?

    Only reached once Stage 1 (`_continues_the_flow`) has already decided
    this reply is NOT a normal continuation — so this prompt's own job is
    narrower than it used to be (picking *which* interrupt, not also
    re-deciding *whether* there is one), but keeps the same defensive
    "only match if truly unrelated" wording rather than trusting Stage 1
    blindly, since a wrong Stage-1 call here is otherwise unrecoverable.
    """
    question_context = f"The agent just asked: {ctx.pending_question!r}\n\n" if ctx.pending_question else ""
    prompt = (
        "A conversational agent is mid-question. The user's reply below has already "
        "been judged to be something other than a normal answer to that question — "
        "which of these global interrupts (things the agent should handle regardless "
        "of what it just asked) does it actually match?\n\n"
        f"{ctx.block()}"
        f"{question_context}"
        f"Candidates:\n{candidate_lines}\n\n"
        "Only match when the reply is clearly, unambiguously one of these — reply "
        "NONE rather than force a match if none genuinely fits.\n\n"
        "Reply with just the number, or NONE if none apply."
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
    """Does `ctx.last_user_message` match a handler trigger or FAQ match phrase?

    Checked on every reply to a `question` node, before that reply is
    treated as the answer to the pending question — mirrors how the
    no-code text target evaluates GLOBAL_HANDLERS/FAQ_POLICY ahead of the
    active state's own ROUTE.

    Two stages (2026-09-17): `_continues_the_flow` first decides whether
    this is a normal continuation at all; only a reply judged NOT a
    continuation goes to `_match_candidate` to pick which interrupt it is.
    One call for the common case (continuing normally), two only when
    something genuinely looks like an interrupt — both stages see the same
    candidate list (see `_continues_the_flow`'s docstring for why Stage 1
    needs it too, not just Stage 2) and the same `history`: recent `"agent:
    ..."` / `"user: ..."` turns (`graph_builder.py`'s `_HISTORY_WINDOW`).
    Without `history`, the only context describing the conversation is one
    bare `pending_question` string, and the model has nothing to judge "is
    this clearly answering it" against beyond a single isolated reply,
    which reliably over-matches a short, on-topic answer against a
    semantically related trigger. Confirmed on real content:
    `family_aims_sam_text_2_0`'s `CL_ASK_NAT` asks for the lead's
    nationality, and the reply `"soy venezolana"` was consistently matched
    to FAQ `F_FOREIGN` (triggers on `"soy extranjero"`) instead of being
    captured as the answer — plausible in isolation ("Venezuelan" and
    "foreigner" are related), wrong once the actual pending question and
    conversation are known.

    Takes the shared `LLMContext` (2026-09-17) instead of `user_reply`/
    `pending_question`/`history` as separate parameters — every runtime LLM
    decision now takes this same type, `graph_builder.py`'s `node_fn` builds
    one per decision point, so this stage sees `ctx.slots` too now (previously
    absent here entirely, unlike almost every other decision call).
    """
    candidates = _candidates(router)
    if not candidates:
        return None
    lines = _candidate_lines(candidates)
    if _continues_the_flow(llm_client, ctx, lines):
        return None
    return _match_candidate(llm_client, candidates, ctx, lines)


def handle_global_router_match(
    match: RouterMatch,
    ctx: LLMContext,
    *,
    resolve_edges: Callable[[list[GraphEdge], LLMContext], str | None],
    on_say: Callable[[GraphNode | FaqNode, list[str]], None] | None = None,
) -> str | None:
    """Resolve where the matched handler/FAQ sends the conversation.

    Parameters:
        match (RouterMatch): The `(kind, node)` pair from `match_global_router`.
        ctx (LLMContext): The session's current context — `resolve_edges` for a
            matched handler gets a copy of this with `goal`/`do` swapped to the
            handler's own (not the originating question node's), since the
            routing decision it's about to make is the handler's, not the
            question's.
        resolve_edges: Given a list of `GraphEdge` (route or fallback),
            return the target of the first satisfied edge, or `None`.
        on_say: Optional callback invoked with `(node, node.say)` so the
            caller can render/emit the matched handler's or FAQ's message.

    Returns:
        `None` to mean "resume the pending question" (the common case — a
        dynamic `[current_state]`/`[resume_state]` target, or no route at
        all), or a concrete state id to jump to via `Command(goto=...)`.
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
    """Does `ctx.last_user_message` also ask something this turn's normal
    handling (capture, FAQs, handlers — already checked by the time this is
    called) has no approved content to address?

    A narrow, cheap judgment call, never a source of new facts: the only
    thing a caller does with `True` is say the fixed, non-specific
    `UNANSWERED_QUESTION_ACK` — this function's own reasoning never reaches
    the user, so there is nothing here for it to hallucinate. Exists
    because a real gap was found live (2026-09-21): a reply that answers
    the pending question *and* asks something extra (e.g. "Soy una pareja
    heterosexual. ¿Eso influye en el proceso?") got its side question
    silently dropped — `render_say` (the only thing rendering the *next*
    turn's message) deliberately never sees `last_user_message`/history at
    all, by design, so it structurally cannot notice or acknowledge it; a
    policy line asking it to was unreachable dead weight, not a fix.
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
        "pending question, a greeting, or a short acknowledgement (\"ok\", \"sí\", "
        "\"gracias\") is NOT this case.\n\n"
        "Reply with just YES or NO."
    )
    return llm_client.complete(prompt).strip().upper().startswith("Y")
