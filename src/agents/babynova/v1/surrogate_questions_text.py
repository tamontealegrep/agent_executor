"""
GHL webhook endpoint for the compiled babynova_surrogate_questions_text agent.

    All the actual plumbing (202 immediately, debounce, session resolution,
    the run_in_threadpool deadlock fix, idempotency, conversations mirror,
    replies via send_ghl_message_async) lives in
    compiled_runner.ghl_endpoint.build_compiled_agent_router, shared by every
    compiled agent wired to GHL -- see agents/family_aims/v1/sam_text.py's own
    docstring for the diagnosis behind each of the fixes baked in there. This
    module is just this agent's config.

    Pilot gate: only messages from BABYNOVA_SURROGATE_QUESTIONS_ALLOWED_PHONES
    are processed by default -- this endpoint is still being validated live,
    so it stays opt-in to a handful of known test numbers. Set
    BABYNOVA_SURROGATE_QUESTIONS_ENFORCE_PHONE_WHITELIST=false to open it to
    every phone once it's cleared to go live, no code change needed.
"""

import os

from agents.helpers.ghl import babynova_ghl_client_config
from compiled_runner.ghl_endpoint import CompiledAgentGhlConfig, build_compiled_agent_router, load_phone_whitelist

SLUG = "babynova_surrogate_questions_text"

ALLOWED_PHONES, ENFORCE_PHONE_WHITELIST = load_phone_whitelist(
    "BABYNOVA_SURROGATE_QUESTIONS_ALLOWED_PHONES",
    "BABYNOVA_SURROGATE_QUESTIONS_ENFORCE_PHONE_WHITELIST",
    default_phones="+573215616921,+573007011593,+573016804227,+573103725324",
)

DEBOUNCE_SECONDS = float(os.getenv("BABYNOVA_SURROGATE_QUESTIONS_DEBOUNCE_SECONDS", "7"))

# How many days back the closing-message sweep still considers a thread --
# NOT the trigger itself (always ~23h45m of inactivity, a WhatsApp platform
# constant), just a safety bound. See CompiledAgentGhlConfig's own docstring.
CLOSING_MESSAGE_LOOKBACK_DAYS = float(os.getenv("BABYNOVA_SURROGATE_QUESTIONS_CLOSING_SWEEP_LOOKBACK_DAYS", "3"))


router = build_compiled_agent_router(
    CompiledAgentGhlConfig(
        slug=SLUG,
        path="/surrogate-questions-text",
        allowed_phones=ALLOWED_PHONES,
        debounce_seconds=DEBOUNCE_SECONDS,
        enforce_phone_whitelist=ENFORCE_PHONE_WHITELIST,
        closing_message_lookback_days=CLOSING_MESSAGE_LOOKBACK_DAYS,
        # config/ghl_defaults.json -- same shared GHL defaults every compiled
        # agent reuses (base_url, timeouts, channel map), authenticated with
        # the BabyNova sub-account's own token (BN_GHL_TOKEN).
        ghl_client_config=babynova_ghl_client_config,
    )
)
