"""Builds a real `langgraph.graph.StateGraph` from a `RuntimeArtifact`.

Consumes the `RuntimeArtifact` `targets.langgraph.runtime_artifact` emits
(a plain, JSON-serializable bundle of the graph definition, global-router
definition, tool contracts, and constants) and builds one LangGraph node
per DSL node, real conditional edges, and `interrupt()` for the multi-turn
"ask a question, wait for the reply" pattern. This is the reference
runtime SPEC.md §2.4 describes as a separate, optional consumer of the
compiled artifact — `dsl/loaders.py`, `dsl/validators.py`,
`dsl/classifier.py`, `dsl/deduplicator.py`, `targets/text/`, `diagrams/`,
`compiler.py`, and `cli.py` are all compiler-only and never imported here.

Deliberately takes a `RuntimeArtifact`, not an `AgentSpec`: a backend that
only *runs* compiled agents should be able to load one from a `.json` file
on disk and never need the YAML-loading, validating, or text-rendering
machinery at all — swapping an agent means replacing that one file, not
redeploying this module. See `targets/langgraph/runtime_artifact.py`.

Known limitations of this reference implementation (documented, not
silently dropped):
  - `subflow_change` nodes are pure pass-through — `AgentSpec` has already
    resolved every route/fallback to concrete, namespaced state ids by the
    time it reaches this module.

`action` node tool-argument binding (`_bind_tool_inputs`, 2026-09-16):
real agent content almost never captures a slot under the exact same
name as the tool contract's declared input (subflow namespacing alone
guarantees a mismatch — `cl__nat` vs. `nationalities`) — an exact-name-only
binder left nearly every tool call unable to run. Three passes, mechanical
before LLM, same philosophy as `_apply_store_line`'s own STORE fallback:
(1) exact slot-name match, (2) a `DO` line shaped `input_name = [slot]` /
`= <CONSTANT>` / `= 'literal'` — the bare-left-hand-side counterpart to
STORE's own `[slot] = ...` grammar, resolved without any LLM call, and
(3) one batched LLM call (never one call per argument) deriving whatever
required inputs are still missing from the full `DO` context and known
slots — covers the DO lines that describe a real transform or derivation
("uppercase", "the literal start_co field of the chosen slot") rather
than a bare copy, which a mechanical parser can't safely resolve on its
own.

Conditional DO/STORE prose (`_apply_conditional_mutations`, 2026-09-16):
a third, distinct DO/STORE convention — "If X, update [slot]" — found live
piloting `family_aims_sam_text_2_0` (`SC_FIX`'s corrections were a total
no-op: the whole node exists to apply a name/phone/email/date correction
and did nothing). Unlike the other two conventions, this one gets no
mechanical pass at all: branching on free text ("if the correction is
about the name", inferring 'surrogacy' from "quiero alguien que lleve a mi
bebé") is exactly the kind of real-language understanding a `question`
node's own capture step already needs an LLM for — a regex here would
silently fail the same way `_bind_tool_inputs`'s exact-match-only binder
did. Any DO/STORE line that isn't a recognized literal assignment but
still names a `[slot]` goes straight to one batched LLM call per node
(every such line together, so the model sees the full instruction set
against one user message, not one call per line). Excluded from `action`
nodes' DO lines — those are entirely owned by `_bind_tool_inputs` above.

`question` nodes are routed exclusively via a `Command(goto=...)` their
own `node_fn` returns — never via `add_conditional_edges`. Verified
empirically: LangGraph does not suppress a node's registered conditional
edges just because that node's function returns a `Command`, so
registering both causes a concurrent-write `InvalidUpdateError` on the
`slots` channel the moment a global-router match resolves to a concrete
jump target (the resume-in-place case never surfaced this, since it never
produces a second write in the same step). See `_resolve_target`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from functools import lru_cache
from types import CodeType
from typing import Any, TypedDict

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, StateGraph
from langgraph.types import Command, interrupt

from engine.dsl.schemas import ToolContractField
from engine.dsl.utils import dynamic_target_slot_name, is_dynamic_target
from engine.runtime.global_router import (
    UNANSWERED_QUESTION_ACK,
    handle_global_router_match,
    has_unanswered_side_question,
    match_global_router,
)
from engine.runtime.llm_client import LLMClient, LLMContext
from engine.runtime.tool_executor import make_tool_executor
from engine.targets.langgraph.condition_parser import NAMED_PREDICATES
from engine.targets.langgraph.graph_renderer import FaqNode, GlobalRouterDefinition, GraphEdge, GraphNode
from engine.targets.langgraph.runtime_artifact import RuntimeArtifact
from engine.targets.langgraph.type_parser import classify_type_expr


logger = logging.getLogger(__name__)


class SessionState(TypedDict):
    slots: dict[str, Any]
    current_state: str
    last_user_message: str
    last_message_at: str
    history: list[str]
    contact: dict[str, Any]


_HISTORY_WINDOW = 5
"""How many recent `"agent: ..."` / `"user: ..."` turns `match_global_router`
gets as context (2026-09-17) — enough to judge whether a reply continues the
conversation it's actually in without carrying the whole transcript into
every routing call."""


def _append_history(history: list[str], entry: str) -> list[str]:
    return (history + [entry])[-_HISTORY_WINDOW:]


# ---------------------------------------------------------------------------
# STORE mutation interpreter (`[slot] = value`, `increment [slot] by 1`)
# ---------------------------------------------------------------------------

_STORE_ASSIGN_RE = re.compile(r"^\[([a-zA-Z_][a-zA-Z0-9_]*)\]\s*=\s*(.+)$")
_STORE_INCREMENT_RE = re.compile(r"^increment\s+\[([a-zA-Z_][a-zA-Z0-9_]*)\]\s+by\s+1$", re.IGNORECASE)
_STORE_SLOT_REF_RE = re.compile(r"^\[([a-zA-Z_][a-zA-Z0-9_]*)\]$")
_STORE_SELF_ARITH_RE = re.compile(r"^\[([a-zA-Z_][a-zA-Z0-9_]*)\]\s*([+-])\s*(\d+)$")
_QUOTED_RE = re.compile(r"^'(.*)'$|^\"(.*)\"$")


def _as_int(value: Any) -> int:
    """`value` as an int, treating `None` and non-numeric content as 0."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _apply_store_line(line: str, ctx: LLMContext, llm_client: LLMClient) -> None:
    """Apply one `STORE`/`DO` mutation line's effect to `ctx.slots`, in place.

    Literal assignments, slot-to-slot copies, `NULL` resets, the
    `increment [x] by 1` phrase, and the DSL's own documented
    `[x] = [x] + 1` retry-counter arithmetic (`NUMERIC_AND_RETRY_COUNTER_
    SEMANTICS` in the text target's system prompt template — this is the
    form real agent content actually uses, not the `increment` phrase) are
    all resolved mechanically. Bare, unquoted prose (e.g. "the IANA
    identifier derived from the reported city") is a computed assignment,
    not a literal — the model is asked to actually perform the described
    computation instead of storing the instruction text itself. A line
    that isn't shaped like an assignment at all (most `DO` lines — tool-
    argument hints, "TOOL CALL ONLY: call X now") is silently a no-op:
    this function is safe to run over `DO` as well as `STORE`.
    """
    slots = ctx.slots
    match = _STORE_INCREMENT_RE.match(line.strip())
    if match:
        name = match.group(1)
        slots[name] = _as_int(slots.get(name)) + 1
        return

    match = _STORE_ASSIGN_RE.match(line.strip())
    if not match:
        return
    name, rhs = match.group(1), match.group(2).strip()

    slot_ref = _STORE_SLOT_REF_RE.match(rhs)
    if slot_ref:
        # A copy from an unset slot never overwrites: use `NULL` explicitly to
        # clear a value. Otherwise a previously captured value (e.g.
        # `resume_state`) is clobbered when the source isn't set in this node.
        source_value = slots.get(slot_ref.group(1))
        if source_value is not None:
            slots[name] = source_value
        return
    arith = _STORE_SELF_ARITH_RE.match(rhs)
    if arith and arith.group(1) == name:
        delta = int(arith.group(3))
        current = _as_int(slots.get(name))
        slots[name] = current + delta if arith.group(2) == "+" else current - delta
        return
    if rhs == "NULL":
        slots[name] = None
        return
    if rhs.lstrip("-").isdigit():
        slots[name] = int(rhs)
        return
    if rhs in ("true", "TRUE", "True"):
        slots[name] = True
        return
    if rhs in ("false", "FALSE", "False"):
        slots[name] = False
        return
    quoted = _QUOTED_RE.match(rhs)
    if quoted:
        slots[name] = quoted.group(1) if quoted.group(1) is not None else quoted.group(2)
        return

    slots[name] = _llm_compute_store_value(llm_client, name, rhs, ctx)


def _llm_compute_store_value(llm_client: LLMClient, slot_name: str, instruction: str, ctx: LLMContext) -> Any:
    policy_block = f"House rules:\n{chr(10).join(ctx.policies)}\n\n" if ctx.policies else ""
    prompt = (
        f"{ctx.block()}"
        f"{policy_block}"
        f"Compute the new value of `{slot_name}` per this instruction: {instruction!r}\n"
        "Reply with just the resulting value, nothing else."
    )
    return llm_client.complete(prompt)


_BRACKET_REF_RE = re.compile(r"\[([a-zA-Z_][a-zA-Z0-9_]*)\]")
_COMPARISON_VERB_RE = re.compile(r"^(compare|validate|check|ensure|verify|confirm|map|treat|evaluate|examine|judge|detect|identify)\b", re.IGNORECASE)


def _is_recognized_mutation_line(line: str) -> bool:
    """True for a DO/STORE line `_apply_store_line` already resolves mechanically."""
    stripped = line.strip()
    return bool(_STORE_INCREMENT_RE.match(stripped) or _STORE_ASSIGN_RE.match(stripped))


def _is_comparison_only_line(line: str) -> bool:
    """True for a DO line that only describes a comparison/validation check —
    almost always context for a sibling `eval: "llm"` ROUTE condition, not an
    instruction to change the slot(s) it names (2026-09-17, found live: `SC_DEC_D`'s
    `"Compare [day] only against the start_local dates in [available_slots]. Do not
    accept approximate, inferred, or unlisted days."` was fed to `_apply_conditional_
    mutations` as if it might be updating `[day]`/`[available_slots]` — the model
    obliged, overwriting `available_slots` (a list of real appointment objects) with
    an arbitrary string, corrupting state a sibling node then rendered straight into
    a user-facing message. A mechanical exclusion on the line's leading verb is a
    safe, narrow guard for this exact shape — a false negative here just means the
    line falls back to being judged by the LLM call below, same as before this
    existed; it can never cause a *new* false positive."""
    return bool(_COMPARISON_VERB_RE.match(line.strip()))


def _apply_conditional_mutations(
    lines: list[str],
    ctx: LLMContext,
    llm_client: LLMClient,
) -> None:
    """Resolve DO/STORE lines shaped as conditional prose ("If X, update Y") — a
    third convention real content uses, distinct from `[slot] = value` (STORE's own
    grammar) and `input_name = [slot]` (action-node tool-argument binding).

    A mechanical parser cannot safely branch on free-text conditions like "if the
    correction is about the name" or infer 'surrogacy' from "quiero alguien que
    lleve a mi bebé" — those require the same real language understanding a
    `question` node's own capture step already relies on. So there is no
    mechanical pass here at all: any DO/STORE line that isn't a recognized literal
    assignment (`_is_recognized_mutation_line`) but still names a `[slot]` goes
    straight to one batched LLM call per node (never one call per line), covering
    every slot any of that node's conditional lines mentions together — so the
    model sees the full picture (e.g. SC_FIX's "name vs phone vs email vs date"
    branches) against the single latest user message, the same way a human agent
    reading all of them at once would. A line with no `[slot]` reference at all
    (e.g. "TOOL CALL ONLY: call check_visa now.") is narrative, not a mutation,
    and is left untouched — same as `_apply_store_line`'s existing behavior.

    The schema is keyed by *slot*, not by line, each with its own boolean
    `applies` gate — deliberately finer-grained than gating per instruction
    line: real content bundles independent per-slot conditions into one line
    ("If the correction is the name, update [c_name]; the phone, [c_phone]; "
    "the email, [c_email]." is three conditions, not one), so a per-line gate
    still forced the model to invent a value for a slot whose own condition
    didn't hold, just because a sibling slot's condition on the same line did.
    A flat, ungated schema was tried even before that and asked the model to
    echo back every untouched slot's current value to signal "no change" —
    which silently corrupted a slot whose real value is a list (e.g.
    `available_slots`, a list of appointment objects) into `null`, since the
    schema's scalar-only types gave the model no way to reproduce a list.
    Gating per slot means a slot whose condition didn't fire is simply never
    touched in `slots`, regardless of what type its current value is.
    """
    conditional_lines = [
        line
        for line in lines
        if not _is_recognized_mutation_line(line)
        and not _is_comparison_only_line(line)
        and _BRACKET_REF_RE.search(line)
    ]
    if not conditional_lines:
        return

    target_slots = sorted({name for line in conditional_lines for name in _BRACKET_REF_RE.findall(line)})
    schema = {
        "type": "object",
        "properties": {
            name: {
                "type": "object",
                "properties": {
                    "applies": {"type": "boolean"},
                    "value": {"type": ["string", "boolean", "integer", "null"]},
                },
                "required": ["applies", "value"],
                "additionalProperties": False,
            }
            for name in target_slots
        },
        "required": target_slots,
        "additionalProperties": False,
    }
    context_lines = "\n".join(ctx.goal)
    instructions = "\n".join(f"- {line}" for line in conditional_lines)
    policy_block = f"House rules:\n{chr(10).join(ctx.policies)}\n\n" if ctx.policies else ""
    prompt = (
        "You are applying conditional update instructions to a conversational agent's "
        "memory slots, based on the user's latest message.\n\n"
        f"This step's purpose:\n{context_lines}\n\n"
        f"Instructions:\n{instructions}\n\n"
        f"{ctx.block()}"
        f"{policy_block}"
        f"For each of these slots — {', '.join(target_slots)} — decide independently "
        "whether an instruction above clearly applies to THAT slot specifically, given "
        "the latest message, and set its `applies` field accordingly. A line naming "
        "several slots together (e.g. 'the name, X; the phone, Y') describes one "
        "condition per slot, not one shared condition — judge each slot on its own. "
        "The latest message may give no real signal for a slot at all (e.g. a bare "
        "greeting) — when you are not reasonably confident the condition is actually "
        "met, set `applies` to false rather than guessing at a value. "
        "Only when a slot's `applies` is true, also set its `value` to the new value "
        "(or null, if the instruction says to clear it). When `applies` is false, "
        "`value` is ignored, so its content does not matter."
    )
    response = llm_client.extract_structured(prompt, schema)
    for name in target_slots:
        entry = response.get(name) or {}
        if entry.get("applies"):
            ctx.slots[name] = entry.get("value")


def _coerce_constant(value: str) -> Any:
    if value.lstrip("-").isdigit():
        return int(value)
    return value


# ---------------------------------------------------------------------------
# Capture extraction (question nodes)
# ---------------------------------------------------------------------------

_LITERAL_MEMBERS_RE = re.compile(r"^Literal\[(.+)\]$")


def _literal_members(type_expr: str) -> list[str] | None:
    match = _LITERAL_MEMBERS_RE.match(type_expr.strip())
    if not match:
        return None
    return [m.strip() for m in match.group(1).split(",")]


def _json_schema_type(type_expr: str) -> dict[str, Any]:
    """Map a `type_expr` to a nullable JSON-Schema property (OpenAI Structured Outputs)."""
    members = _literal_members(type_expr)
    if members:
        return {"type": ["string", "null"], "enum": [*members, None]}
    if type_expr == "bool":
        return {"type": ["boolean", "null"]}
    if type_expr == "int":
        return {"type": ["integer", "null"]}

    # Handle list[Literal[...]] or list[T]
    if type_expr.startswith("list[") and type_expr.endswith("]"):
        inner = type_expr[5:-1].strip()
        inner_schema = _json_schema_type(inner)
        # Drop the "null" from the inner type for the array elements,
        # but the array itself remains nullable in the parent object.
        if isinstance(inner_schema.get("type"), list) and "null" in inner_schema["type"]:
            if len(inner_schema["type"]) == 2:
                inner_schema["type"] = inner_schema["type"][0]
            else:
                inner_schema["type"] = [t for t in inner_schema["type"] if t != "null"]
        if "enum" in inner_schema and None in inner_schema["enum"]:
            inner_schema["enum"] = [e for e in inner_schema["enum"] if e is not None]

        return {
            "type": ["array", "null"],
            "items": inner_schema,
        }

    return {"type": ["string", "null"]}


def _capture_json_schema(capture: list[tuple[str, str]]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {slot: _json_schema_type(type_expr) for slot, type_expr in capture},
        "required": [slot for slot, _ in capture],
        "additionalProperties": False,
    }


def _llm_extract_capture(
    llm_client: LLMClient,
    capture: list[tuple[str, str]],
    ctx: LLMContext,
) -> dict[str, Any]:
    """Ask the model to extract the declared capture slots from the user's reply.

    Takes the shared `LLMContext` (2026-09-17) rather than its own hand-picked
    parameter list — this call site was found live missing `slots` (a `free_text`
    capture like `[day]` has no way to normalize "el 22" against the real dates in
    `[available_slots]` without seeing it) the same way an earlier pass found it
    missing `history`; bundling every decision call onto one context type is the
    fix for that entire *class* of gap, not just this one instance of it.

    `ctx.goal`/`ctx.do` carry the DSL's own normalization instructions (e.g. two
    enum members that must stay distinct categories, not synonyms) — that guidance
    only exists there, nowhere else. `ctx.pending_question` is the question this
    reply is answering. `ctx.policies` (2026-09-18) is the artifact's
    `judgment_policies` — see `LLMContext`'s own docstring.
    """
    context_lines = "\n".join(ctx.goal + ctx.do)
    policy_block = f"House rules:\n{chr(10).join(ctx.policies)}\n\n" if ctx.policies else ""
    prompt = (
        "You are extracting structured data from a chat reply for a conversational "
        "agent's memory slots. The agent just asked:\n"
        f"{ctx.pending_question}\n\n"
        f"{ctx.block()}"
        f"{policy_block}"
        f"Normalization instructions for this step:\n{context_lines}\n\n"
        "IMPORTANT: If a field specifies a list of allowed values (enum/Literal), you MUST "
        "map the user's natural language to those exact values. If the user mentions "
        "multiple items, return them as a list of separate allowed values. "
        "Do not return a single string containing multiple items or 'and'/'y' connectors "
        "inside the list; split them into individual elements.\n\n"
        "Extract the fields defined by the provided schema, following the "
        "normalization instructions above. Use null for anything not clearly stated."
    )
    return llm_client.extract_structured(prompt, _capture_json_schema(capture))


def _coerce_value(value: Any, type_expr: str) -> Any:
    """Best-effort coercion of a value against its declared type_expr.

    Two callers (2026-09-17): a question node's capture extraction (the
    original use — an LLM-extracted value against its `capture` schema type),
    and `_bind_tool_inputs` (a mechanically-resolved tool argument against
    its *contract's own* declared type).
    """
    if value is None:
        return None

    # Handle list[T] or list[Literal[...]]
    if type_expr.startswith("list[") and type_expr.endswith("]"):
        items = []
        if isinstance(value, str):
            value_stripped = value.strip()
            if value_stripped.startswith("[") and value_stripped.endswith("]"):
                try:
                    import json
                    parsed = json.loads(value_stripped.replace("'", '"'))
                    if isinstance(parsed, list):
                        items = parsed
                except Exception:
                    items = [value_stripped]
            else:
                items = [value_stripped] if value_stripped else []
        elif isinstance(value, list):
            items = value
        else:
            items = [value] if value is not None else []

        # Flatten any comma or "y"/"and" separated strings within the list (lazy LLM extraction fix)
        final_items = []
        for item in items:
            if isinstance(item, str):
                # Split by comma, " y ", or " and "
                parts = re.split(r",\s*|\s+y\s+|\s+and\s+", item, flags=re.IGNORECASE)
                final_items.extend([s.strip() for s in parts if s.strip()])
            else:
                final_items.append(item)
        return final_items

    parsed = classify_type_expr(type_expr)
    canonical = parsed.canonical_suggestion if parsed.category == "synonym" else type_expr

    if canonical == "bool":
        if isinstance(value, str):
            return value.lower() in ("true", "1", "yes", "si")
        return bool(value)
    if canonical == "int":
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if canonical == "str":
        if isinstance(value, bool):
            return "si" if value else "no"
        return str(value)
    return value


# ---------------------------------------------------------------------------
# `say` rendering and slot interpolation
# ---------------------------------------------------------------------------

_SUPPORTED_LANGUAGE_NAMES = {"English", "Spanish", "Portuguese"}
_LANGUAGE_NAMES_BY_CODE = {"en": "English", "es": "Spanish", "pt": "Portuguese"}


def _language_name_from_slots(slots: dict[str, Any], fallback: str | None = None) -> str | None:
    """Full language name for `slots["preferred_language"]`, accepting a code or a name.

    Returns `fallback` when the slot is absent or not a supported language.
    The full name (not the 2-letter code) is what reaches the LLM prompts,
    deliberately — it gives the model more context than a bare code does.
    """
    value = slots.get("preferred_language")
    if not isinstance(value, str):
        return fallback
    value = value.strip()
    if value.lower() in _LANGUAGE_NAMES_BY_CODE:
        return _LANGUAGE_NAMES_BY_CODE[value.lower()]
    return value if value in _SUPPORTED_LANGUAGE_NAMES else fallback


_INTERPOLATION_RE = re.compile(r"\[([a-zA-Z_][a-zA-Z0-9_]*)\]|<([A-Z_][A-Z0-9_]*)>")

_ISO_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")
_ISO_TIME_RE = re.compile(r"T(\d{2}):(\d{2})")
_STANDALONE_NUMBER_RE = re.compile(r"\b\d{1,2}\b")
_CONSTANT_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")

_MONTH_NAMES = {
    "Spanish": [
        "enero", "febrero", "marzo", "abril", "mayo", "junio",
        "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    ],
    "English": [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ],
    "Portuguese": [
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    ],
}

_DateParts = tuple[str, str, str]  # (yyyy, mm, dd)


def _appointment_datetime(item: dict[str, Any]) -> str | None:
    start = item.get("start_local") or item.get("start_co")
    return start if isinstance(start, str) else None


def _date_parts(iso_text: str) -> _DateParts | None:
    match = _ISO_DATE_RE.match(iso_text.strip())
    return match.groups() if match else None  # type: ignore[return-value]


def _time_part(iso_text: str) -> str | None:
    match = _ISO_TIME_RE.search(iso_text)
    return f"{match.group(1)}:{match.group(2)}" if match else None


def _format_date_human(date_parts: _DateParts, language_name: str | None) -> str:
    year, month, day = date_parts
    months = _MONTH_NAMES.get(language_name or "Spanish", _MONTH_NAMES["Spanish"])
    if not 1 <= int(month) <= len(months):
        return f"{year}-{month}-{day}"
    month_name = months[int(month) - 1]
    day_number = str(int(day))
    if language_name == "English":
        return f"{month_name} {day_number}, {year}"
    return f"{day_number} de {month_name} de {year}"


def _captured_string_values(slots: dict[str, Any]) -> list[str]:
    """Non-empty string values of `slots`, excluding compile-time constants.

    A constant (`RuntimeArtifact.constants`, seeded into `slots` under its
    exact `UPPER_SNAKE_CASE` name — the same naming convention `_CONST_REF_RE`
    already relies on) never represents anything the user said, so it must
    never be treated as a candidate "the user already picked this date"
    signal. Found live (2026-09-17): `PROCESS_DURATION_MONTHS` (value `"24"`)
    was picked up by the day-of-month heuristic below as if the user had
    already chosen the 24th, before `[day]` was ever asked, silently
    filtering `SC_DAYS`'s very first display down to one wrong day's times.
    Excluding every all-uppercase-named slot closes the whole category, not
    just this one constant — any future constant with a short numeric value
    has the same latent risk.
    """
    return [
        value
        for name, value in slots.items()
        if not _CONSTANT_NAME_RE.match(name) and isinstance(value, str) and value.strip()
    ]


def _find_selected_date(slots: dict[str, Any], candidate_dates: set[_DateParts]) -> _DateParts | None:
    """Does an already-captured slot name one of `candidate_dates`?

    Two passes, most reliable first: (1) a slot whose value is itself an
    ISO date string exactly matching one candidate, (2) only when that
    finds nothing, a slot whose value contains a standalone 1-2 digit
    number matching exactly one candidate's day-of-month — a `free_text`
    capture like `[day]` (e.g. "el 22") is never guaranteed to be
    ISO-normalized, so this loose fallback is what actually fires for real
    content; it only ever acts when the day-of-month is unambiguous among
    the dates on offer, so a coincidental number elsewhere (a retry count,
    a phone digit run — `\\b` never matches inside one) can't misfire into
    a wrong single-day filter. Both passes only ever look at real captured
    slots (`_captured_string_values`), never compile-time constants.
    """
    values = _captured_string_values(slots)

    for value in values:
        parts = _date_parts(value)
        if parts is not None and parts in candidate_dates:
            return parts

    by_day_number: dict[str, set[_DateParts]] = {}
    for parts in candidate_dates:
        by_day_number.setdefault(str(int(parts[2])), set()).add(parts)

    for value in values:
        for number in _STANDALONE_NUMBER_RE.findall(value):
            matches = by_day_number.get(number)
            if matches and len(matches) == 1:
                return next(iter(matches))
    return None


def _format_appointment_list(items: list[dict[str, Any]], slots: dict[str, Any], language_name: str | None) -> str:
    """Format a `[slot]`-shaped list of appointment objects for display.

    The root-cause fix (2026-09-17) for `SC_DAYS`/`SC_HOURS` dumping the
    same raw `start - end; start - end` text regardless of what the DSL's
    own `goal:` asked for ("use only the date part", "filter by [day] and
    use only the time part") — those instructions described a real data
    transformation that no code ever executed, since a `say_verbatim: true`
    block (required here — real appointment data must never be
    paraphrased) never goes through the LLM at all. Mechanical and
    deterministic by design: this is live booking data, zero hallucination
    risk is worth more than handling every possible shape.

    Detects which transformation to apply generically, from data already
    on hand — no new DSL syntax, no hardcoded slot/agent names (chosen
    directly, 2026-09-17): if some other already-captured slot names one of
    this list's dates (`_find_selected_date`), show only that date's times;
    otherwise show the distinct dates on offer. The same mechanism serves
    both `SC_DAYS` (asked before `[day]` exists — no match, shows dates)
    and `SC_HOURS` (asked after `[day]` is captured — matches, shows
    times) without either node needing its own code path.
    """
    dated_items: list[tuple[_DateParts, str | None]] = []
    for item in items:
        start = _appointment_datetime(item)
        parts = _date_parts(start) if start else None
        if parts is not None:
            dated_items.append((parts, _time_part(start)))

    if not dated_items:
        # Not the expected start_local/start_co ISO shape at all -- fall
        # back to the old raw dump rather than silently show nothing.
        parts_out = []
        for item in items:
            start = item.get("start_local") or item.get("start_co", "?")
            end = item.get("end_local") or item.get("end_co", "?")
            parts_out.append(f"{start} - {end}")
        return "; ".join(parts_out)

    candidate_dates = {date_parts for date_parts, _ in dated_items}
    selected_date = (
        next(iter(candidate_dates))
        if len(candidate_dates) == 1
        else _find_selected_date(slots, candidate_dates)
    )

    if selected_date is not None:
        times = sorted({t for date_parts, t in dated_items if date_parts == selected_date and t})
        if times:
            return "\n".join(f"🕐 {t}" for t in times)

    return "\n".join(f"📅 {_format_date_human(d, language_name)}" for d in sorted(candidate_dates))


def _interpolate_slots(text: str, slots: dict[str, Any], language_name: str | None = None) -> str:
    """Replace `[slot_name]` and `<CONSTANT>` references in `say` text with
    their current value.

    Real content authors both forms in `say:` blocks — `[slot]` for a
    captured value, `<CONSTANT>` for a compile-time constant like
    `<AGENT_NAME>`/`<COMPANY_NAME>`/`<PROCESS_DURATION_MONTHS>` — but this
    function only ever substituted the first (2026-09-17: found live,
    `<AGENT_NAME>` etc. showed up literally, unresolved, in every real
    conversation this whole session). `initial_slots()` already seeds every
    compile-time constant into `slots` under its exact uppercase name
    (`RuntimeArtifact.constants`, needed for `MAX_RETRY_ATTEMPTS`-style
    retry-limit conditions to `eval()` at all) — the constant's *value* was
    always one dict lookup away, `_interpolate_slots` just never looked.

    A name missing from `slots` is left as its original bracket token
    (either form) rather than silently blanked, so a real gap stays visible
    instead of disappearing.

    `language_name` (2026-09-17) only matters for a list-of-appointment-
    objects value's human-readable date formatting (`_format_appointment_
    list`) — every other value renders the same regardless.
    """

    def _format_value(value: Any) -> str:
        if isinstance(value, list) and value and isinstance(value[0], dict):
            return _format_appointment_list(value, slots, language_name)
        return str(value)

    def repl(match: re.Match[str]) -> str:
        name = match.group(1) or match.group(2)
        if name not in slots or slots[name] is None:
            return match.group(0)
        return _format_value(slots[name])

    return _INTERPOLATION_RE.sub(repl, text)


def _make_render_say(
    llm_client: LLMClient,
) -> Callable[[str, list[str], bool, str | None, dict[str, Any], list[str] | None], str]:
    """Build a `say`-rendering function."""

    def render_say(
        node_id: str,
        say: list[str],
        say_verbatim: bool,
        language_name: str | None,
        slots: dict[str, Any],
        policies: list[str] | None = None,
        user_message: str = "",
    ) -> str:
        """Render a `say` block, paraphrased naturally in the caller's language via the LLM.

        `say_verbatim: true` blocks are never touched — that flag exists
        specifically to forbid paraphrasing legally-sensitive copy. Every
        other block is paraphrased on every render, deliberately never
        cached: the point (2026-09-16, requested directly — real content
        was reading as scripted and repetitive, especially a question
        re-asked verbatim after a failed capture) is that the same node
        visited twice sounds like a person rephrasing, not a script
        replaying, so a cache that returned the same rendering for the
        same node would defeat that on exactly the retry-loop case this
        was meant to fix. Previously this instructed the model to "return
        it unchanged" if already in the target language, which is the
        wording that produced the scripted-repeat feel in the first place.

        Passes `temperature=1.0` explicitly (2026-09-17) — `complete()`'s
        own default is `0.0`, correct for every other caller's factual
        judgment calls, but wrong here: a paraphrase call at temperature 0
        tends toward the single most-likely rewording, which is exactly
        the boring, repetitive sameness this function exists to avoid.

        `policies` (2026-09-18) is the artifact's pre-filtered `say_policies`
        list (`targets/langgraph/runtime_artifact.py` — `compliance_and_
        scope_rules` + `data_and_variable_rules`, decided at compile time,
        not here). Only the `message`/`terminal` call site in `_make_node_fn`
        passes it for now; `question` phrasing and the global-router's
        matched-handler/FAQ `say` still render without it.

        `user_message` is the user's latest message. It only matters when no
        `language_name` is known yet (`preferred_language` unset or
        unsupported): the paraphrase is then written in the language that
        message is in, so the user can understand what they are being asked
        even before they have picked a language. A known `language_name`
        always wins.
        """
        raw = _interpolate_slots("\n".join(say), slots, language_name)
        if say_verbatim:
            return raw

        in_language = f" in {language_name}" if language_name else ""
        policy_block = (
            f"House rules this message must respect:\n{chr(10).join(policies)}\n\n" if policies else ""
        )
        force_lang = (
            f" Always write the final message in {language_name}. If the text is in another "
            f"language, translate it faithfully into {language_name} before rephrasing."
            if language_name else ""
        )
        if not language_name and user_message.strip():
            force_lang = (
                " Write the final message in the same language the user is writing in, judging by "
                f"their latest message: {user_message.strip()[:200]!r}. If the text is in another "
                "language, translate it faithfully into that language before rephrasing."
            )
        prompt = (
            f"Rephrase this agent message naturally{in_language}, the way a person "
            "would say it in conversation rather than reading a script — vary the "
            "wording and sentence structure. Preserve the exact meaning, every fact, "
            "and any placeholders like <NAME> exactly as written; do not add, remove, "
            f"or soften any information.{force_lang}\n\n"
            f"{policy_block}"
            "Output ONLY the rephrased message itself, "
            "exactly as the agent would send it to the user — no preamble like \"here's "
            "a version of that\", no label, no quotes around it, nothing before or "
            f"after it:\n\n{raw}"
        )
        try:
            return llm_client.complete(prompt, temperature=1.0) or raw
        except Exception:
            logger.warning("say paraphrase failed for node %r; using unparaphrased text", node_id, exc_info=True)
            return raw

    return render_say


# ---------------------------------------------------------------------------
# Edge resolution
# ---------------------------------------------------------------------------


def _matches(value: Any, predicate_name: str) -> bool:
    """Implement the `MATCHES <predicate>` operator for compiled conditions.

    `condition_parser.classify_rule` already refuses to mark a `MATCHES`
    condition mechanical unless `predicate_name` is a registered key in
    `NAMED_PREDICATES` (`targets/langgraph/condition_parser.py`) — this
    function is the runtime counterpart that was missing entirely until
    now: `_EVAL_BUILTINS` never included `_matches`, so a condition
    correctly classified as mechanical (e.g. real content's
    `[document_number] MATCHES numeric_string_without_spaces_or_punctuation`)
    raised `NameError` at eval() time, silently swallowed by
    `resolve_edges`'s `except Exception: continue` — the edge could never
    actually match, regardless of the captured value.
    """
    pattern = NAMED_PREDICATES.get(predicate_name)
    if pattern is None or value is None:
        return False
    return pattern.fullmatch(str(value)) is not None


_EVAL_BUILTINS = {"len": len, "_matches": _matches}


@lru_cache(maxsize=None)
def _compile_expression(expression: str) -> CodeType:
    return compile(expression, "<edge condition>", "eval")


def _make_resolve_edges(
    llm_client: LLMClient,
) -> Callable[[list[GraphEdge], LLMContext], str | None]:
    def resolve_edges(edges: list[GraphEdge], ctx: LLMContext) -> str | None:
        llm_candidates: list[GraphEdge] = []
        for edge in edges:
            if edge.condition_text is None:
                return edge.target
            if edge.is_mechanical and edge.python_expression is not None:
                try:
                    if eval(
                        _compile_expression(edge.python_expression),
                        {"__builtins__": _EVAL_BUILTINS},
                        dict(ctx.slots),
                    ):
                        return edge.target
                except Exception as exc:
                    logger.debug(
                        "edge condition %r -> %r evaluated as false (%s: %s)",
                        edge.python_expression, edge.target, type(exc).__name__, exc,
                    )
                    continue
            else:
                llm_candidates.append(edge)

        if llm_candidates:
            chosen = _llm_pick_condition(llm_client, llm_candidates, ctx)
            if chosen is not None:
                return chosen.target
        return None

    return resolve_edges


def _llm_pick_condition(
    llm_client: LLMClient,
    candidates: list[GraphEdge],
    ctx: LLMContext,
) -> GraphEdge | None:
    """Ask the model which (if any) of the non-mechanical route conditions applies."""
    numbered = "\n".join(f"{i}. {edge.condition_text}" for i, edge in enumerate(candidates))
    context_lines = "\n".join(ctx.goal + ctx.do)
    policy_block = f"House rules:\n{chr(10).join(ctx.policies)}\n\n" if ctx.policies else ""
    prompt = (
        "Given the conversation state below, which of these numbered conditions "
        "is true right now? Reply with just the number, or NONE if none apply.\n\n"
        f"This decision's own instructions:\n{context_lines}\n\n"
        f"{ctx.block()}"
        f"{policy_block}"
        f"Conditions:\n{numbered}"
    )
    text = llm_client.complete(prompt).strip().rstrip(".")
    if text.isdigit() and int(text) < len(candidates):
        return candidates[int(text)]
    return None


# ---------------------------------------------------------------------------
# Node / router function factories
# ---------------------------------------------------------------------------


_ARG_BIND_RE = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*(.+)$")
_CONST_REF_RE = re.compile(r"^<([A-Z_][A-Z0-9_]*)>$")
_ATTR_ACCESS_RE = re.compile(r"^\[([a-zA-Z_][a-zA-Z0-9_]*)\]\.([a-zA-Z_][a-zA-Z0-9_]*)$")
_SEGMENT_SPLIT_RE = re.compile(r",\s*|\.(?=\s|$)")


def _extract_mechanical_arg_bindings(do_lines: list[str], slots: dict[str, Any]) -> dict[str, Any]:
    """Parse `DO` lines for `input_name = <value>` tool-argument hints.

    The bare-left-hand-side counterpart to `_apply_store_line`'s
    `[slot] = ...` grammar: `input_name` here is deliberately NOT
    bracketed (it names a tool input, not a memory slot). Real content
    often crams several of these into one line
    (`"contact_name = [c_name], contact_email = [c_email]."`), so each
    line is split into candidate segments; only segments whose right-hand
    side is a pure `[slot]` reference, a `[slot].field` attribute access,
    a `<CONSTANT>`, or a quoted literal resolve here — anything else (a
    described transformation or derivation) is left for the LLM fallback.

    Splits only on a comma, or a period followed by whitespace/end-of-line
    (2026-09-17, found live) — not on every period. The original
    `[.,]`-only split broke real content shaped `"event_id = [app].
    event_id"` (`AM_DO_CANCEL`/`AM_DO_RESCHED`, `[app]` a captured
    appointment *object*, not a scalar): it chopped the line into
    `"event_id = [app]"` and `"event_id"`, so the first segment
    mechanically bound `event_id` to the *entire* `[app]` dict instead of
    its `event_id` field — a wrong-shaped value silently reaching
    `cancel_appointment`/`edit_appointment`, the same "mechanical pass
    resolves confidently to the wrong thing" failure mode as the untyped
    `duration` bug, just via the line-splitting step instead of missing
    type coercion. A dot immediately followed by an identifier character
    (no space) is attribute access, part of the same segment; a dot
    followed by whitespace or the end of the line is a sentence/clause
    boundary, same as before.
    """
    bindings: dict[str, Any] = {}
    for line in do_lines:
        for segment in _SEGMENT_SPLIT_RE.split(line.strip()):
            segment = segment.strip()
            if not segment:
                continue
            match = _ARG_BIND_RE.search(segment)
            if not match:
                continue
            arg_name, rhs = match.group(1), match.group(2).strip()
            resolved, value = _resolve_simple_rhs(rhs, slots)
            if resolved:
                bindings[arg_name] = value
    return bindings


def _arg_names_mentioned(do_lines: list[str]) -> set[str]:
    """Every argument name a `DO` line assigns to, whether or not the mechanical
    pass could resolve its right-hand side (2026-09-21).

    `_bind_tool_inputs` used to only send *required* fields to the LLM
    fallback — but a `DO` line can name a field the tool contract marks
    optional (`find_appointment`'s `contact_name`/`contact_email`, bound via
    `"contact_name = [name] ?? {{contact.name}}"`, a compound RHS the
    mechanical pass can't resolve). Optional-and-unresolved silently meant
    "never even try the LLM fallback" — the DSL author's explicit DO
    instruction for that field was just dropped. This only widens which
    fields get a chance at the LLM fallback; it never changes what a field
    the DO text never mentions does (still untouched, same as before).
    """
    names: set[str] = set()
    for line in do_lines:
        for segment in _SEGMENT_SPLIT_RE.split(line.strip()):
            match = _ARG_BIND_RE.search(segment.strip())
            if match:
                names.add(match.group(1))
    return names


def _resolve_simple_rhs(rhs: str, slots: dict[str, Any]) -> tuple[bool, Any]:
    slot_ref = _STORE_SLOT_REF_RE.match(rhs)
    if slot_ref:
        return True, slots.get(slot_ref.group(1))
    const_ref = _CONST_REF_RE.match(rhs)
    if const_ref:
        return True, slots.get(const_ref.group(1))
    quoted = _QUOTED_RE.match(rhs)
    if quoted:
        return True, quoted.group(1) if quoted.group(1) is not None else quoted.group(2)
    attr_access = _ATTR_ACCESS_RE.match(rhs)
    if attr_access:
        container = slots.get(attr_access.group(1))
        if isinstance(container, dict):
            return True, container.get(attr_access.group(2))
        return False, None
    return False, None


def _llm_bind_tool_inputs(
    llm_client: LLMClient,
    missing_fields: list[ToolContractField],
    ctx: LLMContext,
) -> dict[str, Any]:
    """One batched call deriving every still-missing required input's value.

    Never one call per argument. Reuses the same JSON-Schema-per-field
    shape `_capture_json_schema` builds for CAPTURE extraction, keyed by
    each field's own `type_expr` — the tool-contract typing work already
    done for real content pays for itself here.

    Also passes each field's own `description`/`examples` from the tool
    contract (2026-09-17, found missing while chasing a real reliability
    issue: `check_visa`'s `nationalities` field is documented as accepting
    "'USA', 'China, India', ['Spain', 'AFG']" — country names or ISO
    codes — but this function only ever fed the model the DO line's own
    wording and a bare JSON-Schema type, discarding that authored spec
    entirely. Without it, deriving a country form from a reported
    nationality (e.g. "venezolana" -> "VEN") sometimes just echoed the raw
    captured wording back — the DO line alone doesn't tell the model what
    the target format actually looks like, only that a conversion is
    wanted).

    `ctx.policies` (2026-09-18) is the artifact's `judgment_policies`
    (`data_and_variable_rules` — "tool results are the only source",
    "never invent availability", "don't mix service tools") — the one
    place this LLM call could otherwise derive a plausible-looking but
    fabricated argument value.
    """
    schema = {
        "type": "object",
        "properties": {f.name: _json_schema_type(f.type_expr or "str") for f in missing_fields},
        "required": [f.name for f in missing_fields],
        "additionalProperties": False,
    }
    specs = "\n".join(
        f"- {f.name}: {f.description}" + (f" Examples: {', '.join(f.examples)}." if f.examples else "")
        for f in missing_fields
    )
    policy_block = f"House rules for this tool call:\n{chr(10).join(ctx.policies)}\n\n" if ctx.policies else ""
    prompt = (
        "You are filling in the arguments for a tool call in a conversational agent.\n\n"
        f"{ctx.block()}"
        f"Instructions for this tool call (from the agent's DO field):\n{chr(10).join(ctx.do)}\n\n"
        f"What each requested argument actually expects (from the tool's own contract):\n{specs}\n\n"
        f"{policy_block}"
        "Determine the value for each requested argument, following the DO instructions above "
        "precisely and matching the format the argument's own contract describes — copy a field "
        "verbatim only where the instructions literally say to copy a slot as-is. If an "
        "instruction describes a derived, converted, or looked-up value (e.g. \"the country name "
        "or code for [nationality]\", \"in uppercase\"), you must actually perform that "
        "derivation — a slot's raw captured wording (e.g. a nationality adjective like "
        "\"Venezuelan\"/\"venezolana\", not the country name \"Venezuela\") is almost never "
        "already in the form the instruction asks for; returning it unchanged when a conversion "
        "was requested is wrong, not a safe default. Never invent a value the instructions don't "
        "support deriving; use null if genuinely unresolvable."
    )
    return llm_client.extract_structured(prompt, schema)


def _bind_tool_inputs(
    contract_inputs: list[ToolContractField],
    ctx: LLMContext,
    llm_client: LLMClient,
) -> dict[str, Any]:
    """Resolve every declared tool input's value for one tool call.

    Three passes, mechanical before LLM — see this module's docstring:
    (1) a slot already exists under the input's exact name, (2) the `DO`
    argument-binding grammar (`_extract_mechanical_arg_bindings`), and
    (3) one batched LLM call for whatever input is still missing after
    both mechanical passes and is either required, or optional but named
    by a `DO` line the mechanical pass couldn't fully resolve
    (`_arg_names_mentioned` — see its own docstring).

    Every resolved value is coerced against its own contract field's
    `type_expr` before returning (2026-09-17, found live — see
    `_coerce_value`'s docstring: a mechanically-bound value, unlike the
    LLM-fallback path, previously never had its type checked against
    anything at all, so a `str`-typed input silently reached the backend
    as whatever raw type its source slot happened to hold).

    `ctx.policies` only reaches the LLM fallback pass (3) — the mechanical
    passes (1)/(2) copy a named slot or a literal `DO` binding verbatim,
    nothing for a policy to guard against.
    """
    slots = ctx.slots
    input_names = {f.name for f in contract_inputs}
    bound: dict[str, Any] = {name: slots[name] for name in input_names if name in slots}

    mechanical = _extract_mechanical_arg_bindings(ctx.do, slots)
    for name, value in mechanical.items():
        if name in input_names and name not in bound:
            bound[name] = value

    mentioned = _arg_names_mentioned(ctx.do)
    optional_mentioned = {f.name for f in contract_inputs if not f.required and f.name in mentioned}
    missing_fields = [
        f for f in contract_inputs if f.name not in bound and (f.required or f.name in mentioned)
    ]
    if missing_fields:
        llm_bound = _llm_bind_tool_inputs(llm_client, missing_fields, ctx)
        # An optional field the LLM couldn't resolve comes back as an explicit
        # `None` (the schema requires *a* value, even if it's null) — dropped
        # here rather than sent on, so it's omitted from the call exactly like
        # before this field ever reached the LLM fallback (the contract's own
        # default, not a `null` the backend's plain `str` schema may reject).
        # A `None` for a *required* field is left as-is: that call was already
        # going to fail without a real value, same as before this change.
        for name, value in llm_bound.items():
            if value is None and name in optional_mentioned:
                continue
            bound[name] = value

    type_by_name = {f.name: f.type_expr for f in contract_inputs if f.type_expr}
    return {
        name: _coerce_value(value, type_by_name[name]) if name in type_by_name else value
        for name, value in bound.items()
    }


def _resolve_target(
    node: GraphNode,
    ctx: LLMContext,
    node_by_id: dict[str, GraphNode],
    resolve_edges: Callable[[list[GraphEdge], LLMContext], str | None],
) -> str:
    """Resolve `node`'s next node id from ROUTE/FALLBACK, defaulting to `END`.

    Shared by `_make_router_fn` (for every non-question node, dispatched via
    `add_conditional_edges`) and `question` nodes' own `node_fn` (dispatched
    via a `Command(goto=...)` instead — see this module's docstring on why
    a question node must never also have `add_conditional_edges`
    registered: LangGraph does not suppress those when a node returns a
    `Command`, so both would fire and collide on the same state channel).

    `ctx` must already be built from `node`'s own `goal`/`do` — callers pass
    the same `LLMContext` they used for this node's other decisions.
    """
    target = resolve_edges(node.route, ctx)
    if target is None:
        target = resolve_edges(node.fallback, ctx)
    if target is None:
        return END

    if is_dynamic_target(target):
        slot_name = dynamic_target_slot_name(target)
        target = ctx.slots.get(slot_name) or END

    if target not in node_by_id:
        return END
    return target


def _leads_to_language_management(node: GraphNode | FaqNode) -> bool:
    """True for a global-router handler whose route enters the `LM__` subflow."""
    return any(edge.target.startswith("LM__") for edge in getattr(node, "route", []))


def _make_node_fn(
    node: GraphNode,
    llm_client: LLMClient,
    router: GlobalRouterDefinition,
    node_by_id: dict[str, GraphNode],
    render_say: Callable[..., str],
    resolve_edges: Callable[[list[GraphEdge], LLMContext], str | None],
    tool_executors: dict[str, Callable[..., dict[str, Any]]],
    tool_input_fields: dict[str, list[ToolContractField]],
    say_callback: Callable[[str], None],
    say_policies: list[str] | None = None,
    judgment_policies: list[str] | None = None,
):
    """Build one LangGraph node function for `node`.

    `say_policies`/`judgment_policies` (2026-09-18) are the artifact's two
    pre-filtered policy line lists (`targets/langgraph/runtime_artifact.py`),
    split by call *purpose*, not node type: `say_policies` reaches every
    `render_say` call this node makes (it takes its own explicit `policies`
    argument, since it has no `ctx`); `judgment_policies` is set once, as
    `policies=` on every `LLMContext` this function builds, so it reaches
    `_llm_extract_capture`, `_apply_conditional_mutations`,
    `_llm_compute_store_value`, `_llm_pick_condition` (via `resolve_edges`),
    and `_llm_bind_tool_inputs` automatically — none of those five take a
    `policies` parameter of their own; they read `ctx.policies`.
    """
    def node_fn(state: SessionState) -> Any:
        slots = dict(state["slots"])
        last_user_message = state.get("last_user_message", "")
        # Expose the incoming state's id to DSL expressions inside this node
        # (e.g. dynamic GO_TO: [current_state] or STORE using [current_state]).
        prev_state_id = state.get("current_state")
        if prev_state_id is not None and "current_state" not in slots:
            slots["current_state"] = prev_state_id
        # The language-management subflow (LM__) returns the user to where
        # they were: capture that resume target once, on entry.
        if node.node_id.startswith("LM__") and prev_state_id is not None and "resume_state" not in slots:
            slots["resume_state"] = prev_state_id
        # Normalize preferred_language to the full language name for rendering/tools
        language_name = _language_name_from_slots(slots)
        history = list(state.get("history", []))
        contact = state.get("contact") or {}

        # One `LLMContext` per decision point (2026-09-17) — rebuilt whenever
        # `history`/`last_user_message` change below (the interrupt loop
        # replaces both), never reused stale. `slots` is never reassigned in
        # this function (only mutated in place), so every ctx built from it
        # always sees the latest values without needing a fresh copy.
        ctx = LLMContext(
            slots=slots,
            history=history,
            last_user_message=last_user_message,
            goal=node.goal,
            do=node.do,
            policies=judgment_policies or [],
            contact=contact,
        )

        # `DO` mutations (retry-counter increments, `[x] = NULL` resets —
        # see NUMERIC_AND_RETRY_COUNTER_SEMANTICS) are applied for every
        # node type, mirroring `STORE` on `registration` nodes below.
        # `DO` was previously only fed to the LLM as route-decision
        # context and never actually mutated `slots`, so every retry
        # Universal mutation pass: Process all mechanical assignments in DO/STORE
        # for ALL node types before any output or execution (2026-10-01).
        # A `DO` line identical to a `STORE` line (common on `registration`
        # nodes) is skipped to avoid double-applying.
        all_mutation_lines = list(node.store) + [
            line for line in node.do if line not in set(node.store)
        ]
        for line in all_mutation_lines:
            _apply_store_line(line, ctx, llm_client)

        # Non-mechanical conditional fallback (only for non-action nodes)
        # 2026-10-01: Enabled for eval: "llm" nodes to allow deriving slots
        # used in subsequent route evaluation (e.g., city typos).
        # Comparison-only lines are still guarded by _is_comparison_only_line.
        if node.node_type != "action":
            _apply_conditional_mutations(all_mutation_lines, ctx, llm_client)

        # Recompute language after DO/STORE so output uses the updated language
        language_name = _language_name_from_slots(slots, fallback=language_name)
        # The language-management subflow exists because the user can't read
        # the current language, so it must not render in it: with no language
        # given, render_say follows the language of the user's own message.
        say_language = None if node.node_id.startswith("LM__") else language_name

        if node.node_type in ("message", "terminal") and node.say:
            rendered_message = render_say(
                node.node_id, node.say, node.say_verbatim, say_language, slots, say_policies,
                user_message=last_user_message,
            )
            say_callback(rendered_message)
            history = _append_history(history, f"agent: {rendered_message}")

        elif node.node_type == "question":
            # ... (question node logic remains identical) ...
            # [OMITTED FOR BREVITY - the original code from line 1133 to 1238]
            # Don't call say_callback here: interrupt() re-runs this function
            # from the top on every resume, so a call before it would repeat
            # on every turn. The caller reads the prompt from the interrupt
            # payload instead, exactly once.
            rendered_say = render_say(
                node.node_id, node.say, node.say_verbatim, say_language, slots, say_policies,
                user_message=last_user_message,
            )

            # Global router: every reply is checked against every handler
            # trigger / FAQ match phrase BEFORE it's treated as the answer
            # to this question (DESIGN_PATTERNS.md P04). A match either
            # re-asks the same pending question (the common "resume where
            # you were" case) or jumps straight to a different state via
            # Command(goto=...) when the matched handler's own routing
            # resolves somewhere else.
            def _on_router_say(n: GraphNode | FaqNode, say: list[str]) -> None:
                nonlocal history
                rendered = render_say(
                    n.node_id if hasattr(n, "node_id") else n.faq_id,
                    say,
                    n.say_verbatim,
                    None if _leads_to_language_management(n) else language_name,
                    slots,
                    say_policies,
                    user_message=reply,
                )
                say_callback(rendered)
                history = _append_history(history, f"agent: {rendered}")

            while True:
                reply = interrupt({"prompt": [rendered_say]})
                history = _append_history(history, f"agent: {rendered_say}")
                history = _append_history(history, f"user: {reply}")
                router_ctx = LLMContext(
                    slots=slots,
                    history=history,
                    last_user_message=reply,
                    pending_question=rendered_say,
                    policies=judgment_policies or [],
                    contact=contact,
                )
                # Top-level firewall: decide if we should refuse outright before
                # attempting any FAQ/Handler matching.
                try:
                    from engine.runtime.global_router import (
                        should_block_message,
                        find_firewall_faq,
                        find_firewall_handler,
                        handle_global_router_match,
                    )
                except Exception:
                    should_block_message = None  # type: ignore
                if callable(should_block_message) and should_block_message(llm_client, router_ctx):
                    # Prefer a dedicated firewall Handler if present: allows routing.
                    firewall_handler = find_firewall_handler(router)
                    if firewall_handler is not None:
                        goto_target = handle_global_router_match(
                            ("handler", firewall_handler),
                            router_ctx,
                            resolve_edges=resolve_edges,
                            on_say=_on_router_say,
                        )
                        if goto_target is not None and goto_target in node_by_id:
                            return Command(
                                update={
                                    "slots": slots,
                                    "current_state": goto_target,
                                    "last_user_message": reply,
                                    "history": history,
                                },
                                goto=goto_target,
                            )
                        # else: no route or dynamic -> re-ask
                        continue

                    # Else use a dedicated firewall FAQ if present: say then resume.
                    firewall_faq = find_firewall_faq(router)
                    if firewall_faq is not None and firewall_faq.say:
                        _on_router_say(firewall_faq, firewall_faq.say)
                        # Re-ask the same pending question
                        continue

                    # Else, no firewall node defined — simply re-ask without extra text.
                    continue
                match = match_global_router(llm_client, router, router_ctx)
                if match is None:
                    last_user_message = reply
                    if has_unanswered_side_question(llm_client, router_ctx):
                        ack = render_say(
                            f"{node.node_id}__side_question_ack",
                            UNANSWERED_QUESTION_ACK,
                            False,
                            say_language,
                            slots,
                            say_policies,
                            user_message=reply,
                        )
                        say_callback(ack)
                        history = _append_history(history, f"agent: {ack}")
                    break
                goto_target = handle_global_router_match(
                    match,
                    router_ctx,
                    resolve_edges=resolve_edges,
                    on_say=_on_router_say,
                )
                if goto_target is not None and goto_target in node_by_id:
                    return Command(
                        update={
                            "slots": slots,
                            "current_state": goto_target,
                            "last_user_message": reply,
                            "history": history,
                        },
                        goto=goto_target,
                    )
                # else: resume where we were — loop back and re-ask.

            ctx = LLMContext(
                slots=slots,
                history=history,
                last_user_message=last_user_message,
                goal=node.goal,
                do=node.do,
                pending_question=rendered_say,
                policies=judgment_policies or [],
                contact=contact,
            )
            if node.capture:
                extracted = _llm_extract_capture(llm_client, node.capture, ctx)
                for slot_name, type_expr in node.capture:
                    if slot_name in extracted:
                        slots[slot_name] = _coerce_value(extracted[slot_name], type_expr)

            # A question node must ALWAYS route via a returned Command, never
            # a plain dict — see _resolve_target's docstring: LangGraph does
            # not suppress add_conditional_edges just because a node returns
            # a Command, so registering both causes a concurrent-write error
            # the moment a global-router match resolves to a concrete jump
            # target. Question nodes therefore never get add_conditional_edges
            # registered (see build_graph) and always resolve their own
            # target here instead.
            target = _resolve_target(node, ctx, node_by_id, resolve_edges)
            return Command(
                update={
                    "slots": slots,
                    "current_state": node.node_id,
                    "last_user_message": last_user_message,
                    "history": history,
                },
                goto=target,
            )

        elif node.node_type == "action" and node.execute:
            executor = tool_executors.get(node.execute)
            if executor is not None:
                inputs = _bind_tool_inputs(tool_input_fields.get(node.execute, []), ctx, llm_client)
                result = executor(**inputs)
                for slot_name, _type_expr in node.capture:
                    if slot_name in result:
                        slots[slot_name] = result[slot_name]
                    else:
                        for key, value in result.items():
                            if slot_name.endswith(key):
                                slots[slot_name] = value
                                break
                
                # Second pass for action nodes: Process STORE/DO mutations
                # AFTER execution, so they can reference captured tool results.
                # (e.g. normalizing a date using the result of time_now).
                for line in all_mutation_lines:
                    _apply_store_line(line, ctx, llm_client)

        elif node.node_type == "registration":
            # Already handled by the universal pass above.
            pass

        return {
            "slots": slots,
            "current_state": node.node_id,
            "last_user_message": last_user_message,
            "history": history,
        }

    return node_fn


def _make_router_fn(
    node: GraphNode,
    node_by_id: dict[str, GraphNode],
    resolve_edges: Callable[[list[GraphEdge], LLMContext], str | None],
    judgment_policies: list[str] | None = None,
):
    def router_fn(state: SessionState) -> str:
        ctx = LLMContext(
            slots=state["slots"],
            history=list(state.get("history", [])),
            last_user_message=state.get("last_user_message", ""),
            goal=node.goal,
            do=node.do,
            policies=judgment_policies or [],
            contact=state.get("contact") or {},
        )
        return _resolve_target(node, ctx, node_by_id, resolve_edges)

    return router_fn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_graph(
    artifact: RuntimeArtifact,
    llm_client: LLMClient,
    *,
    tools_base_url: str = "http://localhost/tools",
    tool_http_client: Any = None,
    say_callback: Callable[[str], None] = print,
    checkpointer: BaseCheckpointSaver | None = None,
) -> Any:
    """Build and compile a real `langgraph.graph.StateGraph` for `artifact`.

    Parameters:
        artifact (RuntimeArtifact): The compiled graph bundle — from
            `targets.langgraph.runtime_artifact.render_runtime_artifact(spec)`
            at compile time, or `runtime_artifact_from_dict(json.load(f))`
            when loaded from a `.json` file a backend swapped in.
        llm_client (LLMClient): Isolates every model call this graph makes.
        tools_base_url (str): Base URL every `action` node's tool executor
            POSTs to (`{tools_base_url}/{tool_name}`).
        tool_http_client: Optional `httpx.Client` shared by every generated
            tool executor — inject a client with a `httpx.MockTransport`
            in tests to avoid real network calls.
        say_callback: Called with the rendered text of every `message` and
            `terminal` node's `SAY` block (and every matched global-router
            handler/FAQ's `SAY`). Defaults to `print`; inject a list-append
            callback in tests instead.
        checkpointer (BaseCheckpointSaver | None): Where session state
            (`[current_state]`, captured slots, ...) is persisted between
            turns. Defaults to `InMemorySaver()` — state is lost on process
            restart, fine for tests and local exploration. A real deployment
            serving live traffic across process restarts (or multiple
            workers) must inject a real one (e.g.
            `langgraph.checkpoint.sqlite.SqliteSaver`,
            `langgraph.checkpoint.postgres.PostgresSaver`) — this function
            never decides that policy itself.

    Returns:
        A compiled LangGraph graph, checkpointed per `checkpointer` above.
    """
    agent_graph = artifact.graph
    node_by_id = {n.node_id: n for n in agent_graph.nodes}
    router = artifact.global_router

    render_say = _make_render_say(llm_client)
    resolve_edges = _make_resolve_edges(llm_client)

    contracts = {c.name: c for c in artifact.tool_contracts}
    tool_executors = {
        name: make_tool_executor(contract, tools_base_url, client=tool_http_client)
        for name, contract in contracts.items()
    }
    tool_input_fields = {name: contract.inputs for name, contract in contracts.items()}

    for node in agent_graph.nodes:
        if node.node_type == "action" and node.execute and node.execute not in contracts:
            logger.warning("node %r executes unknown tool %r; the call will be skipped", node.node_id, node.execute)
        for edge in [*node.route, *node.fallback]:
            if not is_dynamic_target(edge.target) and edge.target not in node_by_id:
                logger.warning("node %r routes to unknown state %r; it will end the graph", node.node_id, edge.target)

    builder = StateGraph(SessionState)
    for node in agent_graph.nodes:
        builder.add_node(
            node.node_id,
            _make_node_fn(
                node, llm_client, router, node_by_id, render_say, resolve_edges,
                tool_executors, tool_input_fields, say_callback,
                say_policies=artifact.say_policies, judgment_policies=artifact.judgment_policies,
            ),
        )
    builder.set_entry_point(agent_graph.entry_point)

    for node in agent_graph.nodes:
        if node.node_type == "terminal":
            builder.add_edge(node.node_id, END)
            continue
        if node.node_type == "question":
            # No add_conditional_edges here — the question node's own
            # node_fn always returns a Command(goto=...) that fully
            # determines routing (see _make_node_fn / _resolve_target).
            continue
        builder.add_conditional_edges(
            node.node_id,
            _make_router_fn(node, node_by_id, resolve_edges, judgment_policies=artifact.judgment_policies),
        )

    return builder.compile(checkpointer=checkpointer or InMemorySaver())


def initial_slots(artifact: RuntimeArtifact) -> dict[str, Any]:
    """Seed constants (e.g. `MAX_RETRY_ATTEMPTS`) into the slot namespace.

    Without this, every retry-limit check referencing a constant would
    raise `NameError` inside `_resolve_edges`'s `eval()` and silently never
    match.
    """
    return {name: _coerce_constant(value) for name, value in artifact.constants.items()}


def fresh_state(
    artifact: RuntimeArtifact,
    contact: dict[str, Any] | None = None,
    seed_history: list[str] | None = None,
    seed_last_user_message: str | None = None,
) -> SessionState:
    """Build the initial `SessionState` for a brand-new conversation.

    `contact` (2026-09-21) is the session's known CRM contact fields (e.g.
    `{"name": ..., "email": ..., "phone": ..., "language": ...}`), matching
    whatever `input_variables.yaml` declares under `contact.*` for this
    agent. Optional and defaults to `{}` — an agent whose DO/ROUTE/SAY text
    never references `{{contact.X}}` is unaffected either way; one that
    does simply sees every such reference as unresolved/unknown until a
    caller supplies real values here.

    `seed_history`/`seed_last_user_message` (2026-09-25) let a caller give
    the very first node some context before anything has actually happened
    in the conversation -- the gap that broke `eval: "llm"` gates shaped
    like an outbound-call agent's "was the call answered?" decision: on a
    genuinely fresh state `history` is `[]`, so `_llm_pick_condition` has
    zero evidence and reliably answers `NONE`, taking the "not answered"
    fallback before the agent ever gets to speak. Both default to the old
    empty values, so every existing caller is unaffected. Neither is
    interpreted here -- a caller decides what "the call connected" or "the
    user already said X" looks like as plain history lines (e.g.
    `f"user: {text}"`), since that phrasing is a harness/UI concern, not
    part of the compiled agent.
    """
    from engine.runtime.session_resolver import now_iso

    return {
        "slots": initial_slots(artifact),
        "current_state": "",
        "last_user_message": seed_last_user_message or "",
        "last_message_at": now_iso(),
        "history": list(seed_history) if seed_history else [],
        "contact": contact or {},
    }
