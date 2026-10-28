"""
Global router for FAQs and handlers during question interrupts.

    Decides whether a user reply is a continuation or an interrupt and,
        if an interrupt, which handler/FAQ applies and where it routes.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from agent_compiler.dsl.utils import is_dynamic_target
from agent_compiler.runtime.llm_client import LLMClient, LLMContext
from agent_compiler.targets.langgraph.graph_renderer import (
    FaqNode,
    GlobalRouterDefinition,
    GraphEdge,
    GraphNode,
)

RouterMatch = tuple[str, "GraphNode | FaqNode"]
"""Tuple ``(kind, node)`` where ``kind`` is ``"handler"`` or ``"faq"``."""

_LANGUAGE_CHANGE_HANDLER_ID = "H_LANG_CHANGE"

_SUPPORTED_LANGUAGE_NAMES = {"English", "Spanish", "Portuguese"}
_LANGUAGE_NAMES_BY_CODE = {"en": "English", "es": "Spanish", "pt": "Portuguese"}


def _language_name_from_slots(slots: dict[str, Any] | None, fallback: str | None = None) -> str | None:
    """Resolve full language name from ``slots['preferred_language']``."""
    if not slots:
        return fallback
    value = slots.get("preferred_language")
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    if value.lower() in _LANGUAGE_NAMES_BY_CODE:
        return _LANGUAGE_NAMES_BY_CODE[value.lower()]
    return value if value in _SUPPORTED_LANGUAGE_NAMES else fallback


def _candidates(router: GlobalRouterDefinition) -> list[RouterMatch]:
    return [("handler", h) for h in router.handlers] + [("faq", f) for f in router.faqs]


def _candidate_lines(candidates: list[RouterMatch]) -> str:
    lines = []
    for i, (kind, node) in enumerate(candidates):
        phrases = node.trigger if kind == "handler" else node.match
        node_id = node.node_id if kind == "handler" else node.faq_id
        lines.append(f"{i}. [{kind}:{node_id}] triggers on: {phrases}")
    return "\n".join(lines)


def _detect_language_and_intent(message: str, current_lang: str) -> tuple[str | None, str]:
    """
    Analyzes `message` for language intent.
    Returns `(detected_language, intent_kind)` where:
      intent_kind is one of: "explicit", "inability_clear", "inability_unclear", "content_answer", "none"
    """
    msg = (message or "").lower().strip()
    if not msg:
        return None, "none"

    # Explicit requests
    explicit_es = any(p in msg for p in [
        "hablame en español", "háblame en español", "habla en español", "habla español",
        "en español por favor", "espanol por favor", "español por favor",
        "cambiar a español", "cambia a español", "escribe en español", "prefiero español",
        "responde en español",
    ]) or msg in {"español", "espanol", "spanish"}

    explicit_pt = any(p in msg for p in [
        "fale em português", "fale em portugues", "em português por favor", "em portugues por favor",
        "mudar para português", "mude para português", "escreva em português", "prefiro português",
        "responda em português",
    ]) or msg in {"português", "portugues", "portuguese"}

    explicit_en = any(p in msg for p in [
        "speak in english", "speak english", "in english please", "switch to english",
        "write in english", "prefer english", "answer in english",
    ]) or msg in {"english", "inglés", "ingles"}

    if explicit_es:
        return "Spanish", "explicit"
    if explicit_pt:
        return "Portuguese", "explicit"
    # Checked before `explicit_en`: "i dont speak english" contains "speak english".
    if any(p in msg for p in [
        "i don't speak english", "i dont speak english", "i don't understand english", "i dont understand english",
    ]):
        return None, "inability_unclear"
    if explicit_en:
        return "English", "explicit"

    # Declared inability
    inability_es = any(p in msg for p in [
        "no hablo ingles", "no hablo inglés", "no entiendo ingles", "no entiendo inglés",
        "no hablo este idioma", "no entiendo este idioma",
    ])
    inability_pt = any(p in msg for p in [
        "nao falo ingles", "não falo inglês", "nao entendo ingles", "não entendo inglês",
        "nao falo este idioma", "não entendo este idioma",
    ])
    inability_en = any(p in msg for p in [
        "i don't speak english", "i dont speak english", "i don't understand english", "i dont understand english",
        "i don't speak spanish", "i dont speak spanish", "i don't understand spanish", "i dont understand spanish",
    ])

    if inability_es:
        return "Spanish", "inability_clear"
    if inability_pt:
        return "Portuguese", "inability_clear"
    if inability_en:
        return None, "inability_unclear"

    # Content answers / general sentences in another language
    es_score = sum(1 for w in [
        "hola", "gracias", "por favor", "español", "espanol", "subrogacion", "subrrogacion",
        "gestacion", "maternidad", "fertilización", "fertilizacion", "interesa", "quiero",
        "método", "metodo", "ropa", "embarazo", "pareja", "nombre", "correo", "viajar",
    ] if w in msg)

    pt_score = sum(1 for w in [
        "olá", "obrigado", "obrigada", "por favor", "português", "barriga", "maternidade",
        "interessa", "quero", "voce", "você", "embrião",
    ] if w in msg)

    en_score = sum(1 for w in [
        "hello", "thanks", "thank you", "please", "surrogacy", "fertility", "interested",
        "looking", "treatment", "method", "process",
    ] if w in msg)

    if es_score >= 1 and es_score > max(pt_score, en_score):
        if current_lang != "Spanish":
            return "Spanish", "content_answer"
    elif pt_score >= 1 and pt_score > max(es_score, en_score):
        if current_lang != "Portuguese":
            return "Portuguese", "content_answer"
    elif en_score >= 1 and en_score > max(es_score, pt_score):
        if current_lang != "English":
            return "English", "content_answer"

    return None, "none"


def _classify_reply(
    llm_client: LLMClient,
    ctx: LLMContext,
    candidate_lines: str,
    language_handler: RouterMatch | None,
) -> str:
    """
Stage 1: prioritize language changes, then classify continuation vs interrupt.

    Parameters:
            llm_client: Client used to execute completion prompts.
            ctx: The current conversation context.
            candidate_lines: Numbered list of possible interrupts.
            language_handler: The reserved language-change handler, if present.

        Returns:
            ``LANGUAGE_CHANGE``, ``CONTINUE``, or ``INTERRUPT``.

    Parameters:
        llm_client: Description.
        ctx: Description.
        candidate_lines: Description.

    Returns:
        The result.
"""
    current_lang = _language_name_from_slots(ctx.slots, fallback="English") or "English"
    question_context = (
        f"The agent just asked (in {current_lang}): {ctx.pending_question!r}\n\n" if ctx.pending_question else ""
    )
    language_priority = ""
    if language_handler is not None:
        language_priority = (
            "LANGUAGE PRIORITY & ALIGNMENT CHECK (HIGHEST PRIORITY):\n"
            "First, evaluate language alignment before classifying continuation or general interrupts:\n"
            f"1. Agent language written to user: {current_lang}\n"
            f"2. User reply language or requested language in latest message: {ctx.last_user_message!r}\n"
            f"3. Are they the same language, or does [preferred_language] need to be updated?\n\n"
            "Rules for language update (in order of priority):\n"
            "Priority 1 - Explicit Request: If the user explicitly asks to switch language "
            "(\"háblame en español\", \"speak English\", \"prefiero portugués\", \"in English please\", \"responde en X\", etc.), "
            "you MUST reply LANGUAGE_CHANGE (or LANGUAGE_CHANGE: Spanish / LANGUAGE_CHANGE: English / LANGUAGE_CHANGE: Portuguese).\n"
            "If the reply is written in another supported language but ALSO answers the pending question (even misspelled, "
            "e.g. 'subrrogacion' to a service question), reply LANGUAGE_CHANGE_ANSWER: <LanguageName> instead, so the answer is kept.\n"
            "Priority 2 - Declared Inability: If the user states they cannot speak or understand the current language "
            "(\"no hablo inglés\", \"I don't speak Spanish\", \"não falo inglês\"), infer the target language from the message if clearly written in another supported language (Spanish, English, or Portuguese). Reply LANGUAGE_CHANGE.\n"
            "Priority 3 - Automatic Detection: If the user's latest message is clearly written in "
            "Spanish, English, or Portuguese, and its language differs from the agent's current language "
            f"({current_lang}), reply LANGUAGE_CHANGE.\n\n"
            "CRITICAL EXCLUSIONS & LOOPS TO AVOID:\n"
            f"- AVOID LOOPS: If the user's language or requested language ALREADY MATCHES the agent's current language ({current_lang}), do NOT reply LANGUAGE_CHANGE.\n"
            "- Do NOT infer a language change from a single word, proper name, city/country name, brand name, number, date, or short answer (\"ok\", \"sí\", \"no\", \"claro\", \"hello\").\n"
            "- A reply that directly and plausibly answers the pending question in context is NOT a language change unless it contains an explicit request or a full sentence in another language.\n"
            "even if it also matches another handler or FAQ.\n\n"
            f"Priority handler: {language_handler[1].node_id}; triggers: {language_handler[1].trigger}\n\n"
        )
        response_options = "LANGUAGE_CHANGE (or LANGUAGE_CHANGE: <LanguageName>), CONTINUE, or INTERRUPT"
    else:
        response_options = "CONTINUE or INTERRUPT"
    prompt = (
        "A conversational agent asked the user a question. Does the user's latest "
        "reply continue answering it normally, in the context of the recent "
        "conversation — or does it match one of the global interrupts listed below "
        "(things the agent should handle regardless of what it just asked)?\n\n"
        f"{ctx.block()}"
        f"{question_context}"
        f"{language_priority}"
        f"Global interrupts:\n{candidate_lines}\n\n"
        "A reply that directly and plausibly answers the pending question is always "
        "a continuation, even if it also loosely resembles one of the interrupts "
        "above (e.g. stating a specific nationality answers a nationality question, "
        "it does not raise a foreign-residency FAQ). A bare one- or two-word reply "
        "(a lone \"sí\"/\"no\"/\"ok\"/\"claro\") is almost always a continuation too — a "
        "genuine interrupt takes more than a couple of words to express, unless it "
        "IS itself, word for word, one of the trigger phrases listed.\n\n"
        f"Reply with just {response_options}."
    )
    decision = llm_client.complete(prompt).strip().upper()
    if decision.startswith("LANGUAGE_CHANGE") and language_handler is not None:
        return decision
    if decision == "INTERRUPT":
        return decision
    return "CONTINUE"


def _match_candidate(
    llm_client: LLMClient,
    candidates: list[RouterMatch],
    ctx: LLMContext,
    candidate_lines: str,
) -> RouterMatch | None:
    """
Stage 2: pick the matching handler/FAQ, if any.

    Parameters:
            llm_client: Client used to execute completion prompts.
            candidates: List of candidate ``(kind, node)`` pairs.
            ctx: The current conversation context.
            candidate_lines: Numbered list of candidates for the prompt.

        Returns:
            The matched ``(kind, node)`` tuple, or None if none apply.

    Parameters:
        llm_client: Description.
        candidates: Description.
        ctx: Description.
        candidate_lines: Description.

    Returns:
        The result.
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
    candidates = _candidates(router)
    if not candidates:
        return None
    lines = _candidate_lines(candidates)
    language_handler = next(
        (
            candidate
            for candidate in candidates
            if candidate[0] == "handler" and candidate[1].node_id == _LANGUAGE_CHANGE_HANDLER_ID
        ),
        None,
    )

    current_lang = _language_name_from_slots(ctx.slots, fallback="English") or "English"
    det_lang, det_intent = _detect_language_and_intent(ctx.last_user_message, current_lang)

    # 1. First check rule-based detection
    language_resolved = False
    if language_handler is not None and det_intent != "none":
        if det_intent == "content_answer" and det_lang:
            # The reply is content in another language: switch language, then still
            # check it against the handlers/FAQs below instead of skipping them.
            if ctx.slots is not None:
                ctx.slots["preferred_language"] = det_lang
            language_resolved = True

        elif det_intent in ("explicit", "inability_clear") and det_lang:
            if ctx.slots is not None:
                ctx.slots["preferred_language"] = det_lang
            synthetic = GraphNode(node_id="H_LANG_REASK", node_type="message", say=[], route=[], fallback=[])
            return ("handler", synthetic)

        elif det_intent == "inability_unclear":
            ask = GraphNode(
                node_id="H_LANG_ASK",
                node_type="message",
                say=[
                    "Puedo atenderte en español, English o português. / "
                    "I can assist you in Spanish, English or Portuguese. / "
                    "Posso atendê-lo em espanhol, inglês ou português."
                ],
                say_verbatim=True,
                route=[],
                fallback=[],
            )
            return ("handler", ask)

    # 2. Fall back to LLM classification
    decision = _classify_reply(llm_client, ctx, lines, None if language_resolved else language_handler)
    if decision.startswith("LANGUAGE_CHANGE") and language_handler is not None:
        upper_dec = decision.upper()
        new_lang = None
        if "SPANISH" in upper_dec or "ESPAÑOL" in upper_dec:
            new_lang = "Spanish"
        elif "ENGLISH" in upper_dec or "INGLÉS" in upper_dec or "INGLES" in upper_dec:
            new_lang = "English"
        elif "PORTUGUESE" in upper_dec or "PORTUGUÉS" in upper_dec or "PORTUGUES" in upper_dec:
            new_lang = "Portuguese"
        elif det_lang:
            new_lang = det_lang

        if new_lang and ctx.slots is not None:
            ctx.slots["preferred_language"] = new_lang

        # Check if the user message is a content answer
        last_msg = (ctx.last_user_message or "").lower()
        is_content = "_ANSWER" in upper_dec or any(w in last_msg for w in [
            "subrogacion", "subrrogacion", "fiv", "ivf", "ropa", "in vitro", "gestacion",
            "maternidad", "pareja", "mi nombre", "me interesa", "quiero",
        ])
        if is_content:
            return None  # Continue with answer!

        synthetic = GraphNode(node_id="H_LANG_REASK", node_type="message", say=[], route=[], fallback=[])
        return ("handler", synthetic)

    if decision == "CONTINUE":
        return None
    return _match_candidate(llm_client, candidates, ctx, lines)


def match_side_question(
    llm_client: LLMClient,
    router: GlobalRouterDefinition,
    ctx: LLMContext,
    exclude_ids: frozenset[str] = frozenset(),
) -> RouterMatch | None:
    """
    The FAQ or handler that answers a question tucked into a reply, if one does.

    Stage 1 sometimes calls such a reply a plain answer ("what documents do I need?" to a yes/no
    question), so the question would reach the generic advisor fallback even when a specific FAQ
    covers it. `exclude_ids` holds the fallback itself, which must not match here.
    """
    candidates = [
        candidate
        for candidate in _candidates(router)
        if getattr(candidate[1], "node_id", None) not in exclude_ids
        and getattr(candidate[1], "faq_id", None) not in exclude_ids
    ]
    if not candidates:
        return None
    return _match_candidate(llm_client, candidates, ctx, _candidate_lines(candidates))


def handle_global_router_match(
    match: RouterMatch,
    ctx: LLMContext,
    *,
    resolve_edges: Callable[[list[GraphEdge], LLMContext], str | None],
    on_say: Callable[[GraphNode | FaqNode, list[str]], None] | None = None,
) -> str | None:
    """
Resolve where a matched handler/FAQ routes the conversation.

    Parameters:
            match: The ``(kind, node)`` pair from ``match_global_router``.
            ctx: Session context; passed to ``resolve_edges`` with handler goal/DO.
            resolve_edges: Evaluates route/fallback edges to a target id or None.
            on_say: Optional callback to emit the handler/FAQ message.

        Returns:
            Target node id, or None to resume the pending question.

    Parameters:
        match: Description.
        ctx: Description.
        resolve_edges: Description.
        on_say: Description.

    Returns:
        The result.
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
# Fixed, non-specific deflection used when a side question is detected.


_QUESTION_CUE_RE = re.compile(
    r"\b(cu[aá]nto|cu[aá]nta|cu[aá]nt[oa]s|cu[aá]l(es)?|c[oó]mo|qu[eé]|por ?qu[eé]|d[oó]nde|cu[aá]ndo|qui[eé]n(es)?"
    r"|puedo|puede|pueden|h[aá]blame|hablame|cu[eé]ntame|expl[ií]came|dime|d[ií]game|quiero saber|necesito saber"
    r"|how|what|why|when|where|which|who|can i|could you|can you|tell me|explain|is it|are there|do you|does it"
    r"|quanto|quanta|qual|quais|onde|quando|quem|por que|me conte|me explique|pode)\b",
    re.IGNORECASE,
)


def _looks_like_question(message: str) -> bool:
    """Cheap mechanical gate: does `message` contain a question mark or an interrogative/request cue?"""
    text = (message or "").strip()
    if not text:
        return False
    if "?" in text or "¿" in text:
        return True
    return bool(_QUESTION_CUE_RE.search(text))


def has_unanswered_side_question(llm_client: LLMClient, ctx: LLMContext) -> bool:
    """
Detect whether the latest reply includes an unanswered side question.

    Parameters:
            llm_client: Client used to execute completion prompts.
            ctx: The current conversation context.

        Returns:
            True if a side question is present; otherwise False.

    Parameters:
        llm_client: Description.
        ctx: Description.

    Returns:
        The result.
"""
    if not _looks_like_question(ctx.last_user_message):
        return False
    policy_block = f"House rules:\n{chr(10).join(ctx.policies)}\n\n" if ctx.policies else ""
    pending_block = f"The question the agent just asked: {ctx.pending_question!r}\n\n" if ctx.pending_question else ""
    prompt = (
        "A conversational agent just received the user's latest reply below. It has "
        "already been checked against every specific FAQ and handler this agent has, "
        "and none matched — it will be captured normally as an answer to the pending "
        "question.\n\n"
        f"{ctx.block()}"
        f"{pending_block}"
        f"{policy_block}"
        "Does this reply ALSO contain a genuine, separate question or request for "
        "specific information (a fact, a number, a procedure detail, a price, a "
        "personal-situation judgment) that the user wants the agent to answer, and "
        "that is not already covered by the conversation so far? It is NOT this case "
        "when the reply is:\n"
        "- a plain answer to the pending question, even if phrased as a question "
        "(\"¿yo? soy de Colombia\", \"en pareja, ¿no?\");\n"
        "- a request to repeat, clarify or rephrase the pending question itself;\n"
        "- a greeting, small talk, a rhetorical remark, or an acknowledgement "
        "(\"ok\", \"sí\", \"gracias\", \"¿listo?\");\n"
        "- a statement about the user's own situation with no information requested.\n\n"
        "Reply YES only if you can quote the exact separate question or request from "
        "the reply; otherwise reply NO. Reply with just YES or NO."
    )
    return llm_client.complete(prompt).strip().upper().startswith("Y")
