# Compilation Report — `family_aims_sam_text`

**Status:** ✅ Clean — compiled artifacts (text/, langgraph/) were written.

## Summary

| Check | Result |
| :--- | :--- |
| Validation errors | 0 |
| Validation warnings | 14 |
| Duplicate rule occurrences | 0 (across 0 group(s)) |
| Unreachable states | 0 |
| States with no static incoming edges | 0 |
| Unreachable terminal states | 0 |

---

## Validation Report

- Errors: 0
- Warnings: 14

### Errors
- None

### Warnings
- **[ACTION_WITHOUT_CAPTURE]** `state.LM__LM_ACT_SYNC`: The state executes tool 'update_custom_field', which declares outputs, but the state captures none of them.
- **[UNUSED_MEMORY_SLOT]** `memory_slots`: Memory slot 'lm__language_update_success' is declared but not used anywhere in the aggregated spec.
- **[UNUSED_MEMORY_SLOT]** `memory_slots`: Memory slot 'resume_state' is declared but not used anywhere in the aggregated spec.
- **[REDUNDANT_FALLBACK]** `state.LM__LM_ACT_SYNC`: ROUTE and FALLBACK always send to the same target 'LM__LM_RESUME'.
- **[EVAL_LLM_EXPLICIT]** `state.AM__AM_INIT`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.AM__AM_ASK_NAME`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.SC__SC_DEC_N_CRM`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.SC__SC_DEC_P_CRM`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.SC__SC_DEC_E_CRM`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.SC__SC_AV_I`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.SC__SC_AV_S`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.SC__SC_DEC_D`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.SC__SC_DEC_S`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.
- **[EVAL_LLM_EXPLICIT]** `state.SC__SC_MORE`: eval='llm' — this node's routing is an explicit LLM judgment call, not a mechanical comparison. Prefer capturing the judgment as a typed CAPTURE in a preceding node and comparing that slot here instead, where possible.

---

## Deduplication Report

- No duplicate rules found.

---

## Orphan State Report

- Agent: `family_aims_sam_text`
- Total states: 221
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
