"""Isolates every LLM call the runtime makes behind one seam.

Not a multi-provider abstraction layer (SPEC.md §4 non-objectives) — just
one seam (`OpenAILLMClient`) instead of `client.chat.completions.create`
scattered across `graph_builder.py` and `global_router.py`. Tests inject
a fake object implementing the same two methods; nothing else in
`runtime/` imports `openai` directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class LLMContext:
    """The context every runtime LLM decision call is given, normalized (2026-09-17).

    Before this, each decision function (`_llm_extract_capture`, `_apply_conditional_
    mutations`, `_llm_pick_condition`, `_llm_bind_tool_inputs`, `_llm_compute_store_
    value`, the global router's two stages...) hand-picked its own subset of
    `slots`/`history`/`last_user_message`/`goal`/`do` as positional parameters, added
    one at a time whenever a live bug traced back to a specific call site missing one
    (`_llm_extract_capture` never saw `slots`, `_llm_compute_store_value` never saw
    `history`, non-question-node routing never saw `history` at all...). Bundling the
    full picture into one type, built once per decision point from data `node_fn`
    already has in scope, means a field is either on every call or an explicit,
    visible `[]`/`""` at the call site — never silently absent because nobody thought
    to add it yet.

    `goal`/`do` default to empty — the global router's two stages use this same type
    but have no single node's `goal`/`do` to offer (a router decision spans multiple
    handler/FAQ candidates, not one node's own purpose).

    `policies` (2026-09-18) is the artifact's `judgment_policies` — the
    `data_and_variable_rules` lines `targets/langgraph/runtime_artifact.py`
    pre-filters at compile time (normalization, "tool results are the only
    source", "no hallucinations"). Every ctx-based judgment call
    (`_llm_extract_capture`, `_apply_conditional_mutations`,
    `_llm_compute_store_value`, `_llm_pick_condition`, `_llm_bind_tool_inputs`)
    reads it straight off `ctx`, so it only needs setting once, where each
    `ctx` is built (`_make_node_fn`, `_make_router_fn`) — never as a
    separate parameter threaded through every function above. `render_say`
    is the one exception: it doesn't take a `ctx` at all, so it keeps its
    own explicit `policies` argument (fed the artifact's separate
    `say_policies`, a different section list — see `runtime_artifact.py`).

    `contact` (2026-09-21) is the session's known CRM contact fields —
    whatever `input_variables.yaml` declares under the `contact.*`
    namespace (`contact.name`, `contact.email`, ...), seeded once into
    `SessionState` at `fresh_state()` and read from there on every node.
    Found live: DO/ROUTE/STORE lines referencing `{{contact.name}}` (e.g.
    `appointment_management`'s "contact_name = [name] ?? {{contact.name}}")
    had nothing to resolve against — no field on this type carried the
    session's real contact data at all, so every `{{contact.X}}` reference
    reached an LLM call as bare, ungrounded template text. This is not
    resolved as a new mechanical grammar (unlike `[slot]`); it is exposed
    the same way `slots`/`history` already are — as data in `block()` —
    and every prompt that might encounter a `{{contact.X}}` reference in
    its own DO/ROUTE/SAY text is told, once, what that syntax means.
    """

    slots: dict[str, Any]
    history: list[str]
    last_user_message: str
    goal: list[str] = field(default_factory=list)
    do: list[str] = field(default_factory=list)
    pending_question: str = ""
    policies: list[str] = field(default_factory=list)
    contact: dict[str, Any] = field(default_factory=dict)

    def block(self) -> str:
        """Render the context pieces every decision prompt includes the same way.

        Deliberately excludes `goal`/`do`/`pending_question` — unlike `history`/
        `slots`/`last_user_message`, which every call site wants presented
        identically, those three are used differently enough by different callers
        (merged with other instructions, shown as the specific lines being
        resolved, framed as "the agent just asked") that forcing one shared
        rendering would either duplicate content a caller already shows itself or
        change wording tuned against a live regression. Callers render those
        themselves.
        """
        history_block = f"Recent conversation:\n{chr(10).join(self.history)}\n\n" if self.history else ""
        contact_block = (
            f"Contact info already on file for this session (from the CRM): "
            f"{json.dumps(self.contact, default=str)}\n\n"
            "A condition elsewhere that reads like `{{contact.X}} != null AND {{contact.X}} != "
            "'{{contact.X}}'` is NOT a literal self-comparison — it is checking whether the CRM "
            "actually has that field filled in, versus the field still showing the unsubstituted "
            "merge-tag placeholder. If the contact info above has a real value for X, treat the "
            "WHOLE expression as TRUE. Only treat it as FALSE if X is missing/null above.\n\n"
            if self.contact
            else ""
        )
        return (
            f"{history_block}"
            f"{contact_block}"
            f"Known slot values: {json.dumps(self.slots, default=str)}\n\n"
            f"The user's latest message: {self.last_user_message!r}\n\n"
        )


class LLMClient(Protocol):
    """The only two LLM operations the runtime needs.

    Any object exposing these two methods works — tests pass a small fake,
    production code passes `OpenAILLMClient`.
    """

    def complete(self, prompt: str, *, temperature: float = 0.0) -> str:
        """Return the model's raw text completion for `prompt`.

        `temperature` defaults to `0.0` because most callers (`match_global_
        router`, `_llm_pick_condition`, `_llm_compute_store_value`) are
        making a factual judgment call — "does this match", "which
        condition is true", "what is this value" — where consistency across
        otherwise-identical calls matters, not variety. The one caller that
        wants the opposite (`render_say`'s SAY paraphrasing, 2026-09-16)
        passes a higher `temperature` explicitly.
        """
        ...

    def extract_structured(self, prompt: str, json_schema: dict[str, Any]) -> dict[str, Any]:
        """Return a dict conforming to `json_schema`, extracted from `prompt`."""
        ...


class OpenAILLMClient:
    """Default `LLMClient` backed by the OpenAI Chat Completions API."""

    def __init__(self, api_key: str | None = None, model: str = "gpt-4o-mini") -> None:
        from openai import OpenAI  # local import — importing this module never requires an API key

        self._client = OpenAI(api_key=api_key)
        self._model = model

    def complete(self, prompt: str, *, temperature: float = 0.0) -> str:
        response = self._client.chat.completions.create(
            model=self._model, messages=[{"role": "user", "content": prompt}], temperature=temperature
        )
        return (response.choices[0].message.content or "").strip()

    def extract_structured(self, prompt: str, json_schema: dict[str, Any]) -> dict[str, Any]:
        """Every caller of this method wants a factual extraction/derivation
        (capture a slot, bind a tool argument, resolve a conditional mutation)
        — never creative variation, unlike `complete()` (also used for SAY
        paraphrasing, which wants exactly that). `temperature=0` measurably
        improved reliability on a real, previously-flaky case: deriving a
        country name/code from a reported nationality (e.g. "venezolana" ->
        "VEN") — without it, the model would sometimes just echo the raw
        captured wording back unchanged instead of performing the requested
        conversion, non-deterministically, across otherwise-identical calls.
        """
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "extraction", "schema": json_schema, "strict": True},
            },
        )
        try:
            return json.loads(response.choices[0].message.content or "{}")
        except json.JSONDecodeError:
            return {}
