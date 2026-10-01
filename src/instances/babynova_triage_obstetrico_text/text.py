"""GHL webhook endpoint for the compiled babynova_triage_obstetrico_text agent.

All the actual plumbing (202 immediately, debounce, session resolution,
the run_in_threadpool deadlock fix, idempotency, conversations mirror,
replies via send_ghl_message_async) lives in
compiled_runner.ghl_endpoint.build_compiled_agent_router, shared by every
compiled agent wired to GHL -- see agents/family_aims_sam/text.py's own
docstring for the diagnosis behind each of the fixes baked in there. This
module is just this agent's config.

Known gap (2026-09-28): the flow's two action nodes --
TRIAGE_CONTACT_PATIENT_SUPPORT (contacto_ap) and TRIAGE_PRIORITY_APPOINTMENT
(citas_prioritarias) -- call tools that are not implemented under any
src/tools/*/ app yet (see compiled_runner/loader.py's _TOOLS_APP_BY_SLUG
and TODO.md). Wiring this endpoint anyway was a deliberate, explicit choice
(not an oversight): the phone allowlist below keeps it opt-in to the same
known test numbers as family_aims_sam_text, so nothing beyond internal
testing can hit those two nodes and get a ToolExecutionError until the
tools exist.

Pilot gate (2026-09-28): only messages from BABYNOVA_TRIAGE_ALLOWED_PHONES
are processed by default -- this endpoint is still being validated live,
so it stays opt-in to a handful of known test numbers. Both the number
list and whether the gate applies at all live in .env now (2026-09-29,
moved out of a hardcoded frozenset here) -- see load_phone_whitelist's own
docstring. DO NOT set BABYNOVA_TRIAGE_ENFORCE_PHONE_WHITELIST=false until
the gap above is closed -- that flag is what keeps real WhatsApp traffic
away from the two nodes that would currently fail.
"""

import os

from instances.helpers.ghl import default_ghl_client_config
from compiled_runner.ghl_endpoint import CompiledAgentGhlConfig, build_compiled_agent_router, load_phone_whitelist

SLUG = "babynova_triage_obstetrico_text"

ALLOWED_PHONES, ENFORCE_PHONE_WHITELIST = load_phone_whitelist(
    "BABYNOVA_TRIAGE_ALLOWED_PHONES",
    "BABYNOVA_TRIAGE_ENFORCE_PHONE_WHITELIST",
    default_phones="+573215616921,+573007011593,+573016804227,+573103725324",
)

DEBOUNCE_SECONDS = float(os.getenv("BABYNOVA_TRIAGE_DEBOUNCE_SECONDS", "15"))

# How many days back the closing-message sweep still considers a thread --
# NOT the trigger itself (always ~23h45m of inactivity, a WhatsApp platform
# constant), just a safety bound. See CompiledAgentGhlConfig's own docstring.
CLOSING_MESSAGE_LOOKBACK_DAYS = float(os.getenv("BABYNOVA_TRIAGE_CLOSING_SWEEP_LOOKBACK_DAYS", "3"))


router = build_compiled_agent_router(
    CompiledAgentGhlConfig(
        slug=SLUG,
        path="/triage_text",
        allowed_phones=ALLOWED_PHONES,
        debounce_seconds=DEBOUNCE_SECONDS,
        enforce_phone_whitelist=ENFORCE_PHONE_WHITELIST,
        closing_message_lookback_days=CLOSING_MESSAGE_LOOKBACK_DAYS,
        # config/ghl_defaults.json -- agent-agnostic, same shared GHL config
        # every compiled agent reuses (base_url, timeouts, channel map).
        ghl_client_config=default_ghl_client_config,
    )
)
