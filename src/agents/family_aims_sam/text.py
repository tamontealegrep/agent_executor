"""GHL webhook endpoint for the compiled family_aims_sam_text agent.

All the actual plumbing (202 immediately, debounce, session resolution,
the run_in_threadpool deadlock fix, idempotency, conversations mirror,
replies via send_ghl_message_async) lives in
compiled_runner.ghl_endpoint.build_compiled_agent_router, shared by every
compiled agent wired to GHL. This module is just this agent's config.

"Memory" is the graph's own checkpointer (see compiled_runner/loader.py):
session state is keyed by thread_id and survives across turns without
re-fetching GHL history.

Pilot gate (2026-09-28): only messages from ALLOWED_TEST_PHONES are
processed -- this endpoint is still being validated live, so it stays
opt-in to a handful of known test numbers.
"""

import os

from agents.helpers.ghl import default_ghl_client_config
from compiled_runner.ghl_endpoint import CompiledAgentGhlConfig, build_compiled_agent_router

SLUG = "family_aims_sam_text"

ALLOWED_TEST_PHONES = frozenset(
    {
        "+573215616921",
        "+573007011593",
        "+573016804227",
        "+573103725324",
    }
)

DEBOUNCE_SECONDS = float(os.getenv("SAM_TEXT_DEBOUNCE_SECONDS", "15"))


router = build_compiled_agent_router(
    CompiledAgentGhlConfig(
        slug=SLUG,
        path="/sam_text",
        allowed_phones=ALLOWED_TEST_PHONES,
        debounce_seconds=DEBOUNCE_SECONDS,
        # config/ghl_defaults.json -- agent-agnostic, not the classic Sam's
        # agent.json (see that file's own note on why this was split out).
        ghl_client_config=default_ghl_client_config,
    )
)
