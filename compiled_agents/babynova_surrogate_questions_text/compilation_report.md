# Compilation Report — `babynova_surrogate_questions_text`

**Status:** ✅ Clean — compiled artifacts (text/, langgraph/) were written.

## Summary

| Check | Result |
| :--- | :--- |
| Validation errors | 0 |
| Validation warnings | 19 |
| Duplicate rule occurrences | 0 (across 0 group(s)) |
| Unreachable states | 0 |
| States with no static incoming edges | 0 |
| Unreachable terminal states | 0 |

---

## Validation Report

- Errors: 0
- Warnings: 19

### Errors
- None

### Warnings
- **[CAPTURE_NOT_IN_CONTRACT_OUTPUTS]** `state.Q__SQ_CHECK_DOCUMENTATION`: The state captures slots ['documentation'] that do not appear as outputs of contract 'check_documentation'.
- **[UNUSED_MEMORY_SLOT]** `memory_slots`: Memory slot 's__c_email' is declared but not used anywhere in the aggregated spec.
- **[UNUSED_MEMORY_SLOT]** `memory_slots`: Memory slot 's__c_name' is declared but not used anywhere in the aggregated spec.
- **[UNUSED_MEMORY_SLOT]** `memory_slots`: Memory slot 's__c_phone' is declared but not used anywhere in the aggregated spec.
- **[REDUNDANT_FALLBACK]** `state.Q__SQ_ASK_CITY_RETRY`: ROUTE and FALLBACK always send to the same target 'Q__SQ_DECIDE_CITY'.
- **[REDUNDANT_FALLBACK]** `state.Q__SQ_CHECK_DOCUMENTATION`: ROUTE and FALLBACK always send to the same target 'Q__SQ_ASK_DRUGS'.
- **[REDUNDANT_FALLBACK]** `state.Q__SQ_RUN_CLASSIFICATION`: ROUTE and FALLBACK always send to the same target 'Q__SQ_DEC_ELIGIBILITY'.
- **[EVAL_LLM_EXPLICIT]** `state.O__OP_HAS_NAME`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.Q__SQ_DEC_CITY_VAL`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.Q__SQ_DEC_ELIGIBILITY`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.Q__SQ_RESOLVE_MISSING`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.S__SC_DEC_DAY`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.S__SC_DEC_SLOT`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.S__SC_MORE`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.AM__AM_INIT`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.AM__AM_DECIDE_FIND`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.AM__AM_DECIDE_CANCEL`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.AM__AM_DECIDE_RESCHED_AV`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.AM__AM_DECIDE_EDIT`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.

---

## Deduplication Report

- No duplicate rules found.

---

## Orphan State Report

- Agent: `babynova_surrogate_questions_text`
- Total states: 126
- Unreachable states: 0
- States with no static incoming edges: 0
- Unreachable terminal states: 0

> Notes
> - `No static incoming edges` means no explicit static predecessor was found in `ROUTE`, `FALLBACK`, or `FAQ_POLICY`.
> - Dynamic targets such as `GO_TO: [resume_state]` are **not** counted as explicit predecessors.
> - `START_AT` and any static FAQ entry states discovered from `FAQ_POLICY` are treated as valid entry states, not as orphans.

### Valid entry states

- `MESSAGE_START`

### Unreachable states

- None

### States with no static incoming edges

- None

### Unreachable terminal states

- None
