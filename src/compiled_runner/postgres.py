"""Postgres wiring for the compiled-agent pipeline: the LangGraph
checkpointer (conversation replay state), a small `conversations` table we
own ourselves, and the closing-message sweep that reads it.

Why a separate table when the checkpointer already persists everything:
LangGraph's own `checkpoints`/`checkpoint_blobs` tables are internal replay
plumbing, not a queryable business table. `checkpoints.checkpoint` (JSONB)
only inlines primitive channel values (plain str/int/float/bool) -- our
`current_state`/`last_user_message`/`last_message_at` land there, but
`slots` (which holds `preferred_language`), `history`, and `contact` are
dicts/lists, so LangGraph pops them out and stores them separately in
`checkpoint_blobs.blob` as msgpack-encoded bytes (see `put()` in
`langgraph/checkpoint/postgres/__init__.py`) -- not plain SQL-queryable
JSON. There's also no column anywhere for *which agent* a thread belongs
to; a checkpointer is generic across any graph that uses it.

`conversations` is a thin, denormalized, query-friendly mirror -- one row
per thread_id, upserted after each turn -- for anything you'd want to ask
with plain SQL (which agent, what language, is this contact idle, what did
they last say) without touching msgpack blobs. Shared by every compiled
agent (see compiled_runner/ghl_endpoint.py) -- `agent_slug` plus
`slots_summary` being a schemaless JSONB, not fixed columns per business
field, is what lets one table serve agents with completely different slot
vocabularies (family_aims_sam_text's `vi__treat` vs. some future agent's
own fields) without a migration per agent. It's also the durable source
`find_conversations_needing_closing_message` sweeps -- durable specifically
because in-memory timers (like the debounce in ghl_endpoint.py) don't
survive a process restart, and this one has to survive ~24h.
"""

from __future__ import annotations

import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


def postgres_enabled() -> bool:
    return bool(DATABASE_URL)


CONVERSATIONS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conversations (
    thread_id TEXT PRIMARY KEY,
    agent_slug TEXT NOT NULL,
    location_id TEXT NOT NULL,
    contact_id TEXT NOT NULL,
    contact_name TEXT,
    contact_phone TEXT,
    contact_email TEXT,
    channel TEXT,
    preferred_language TEXT,
    current_state TEXT,
    last_user_message TEXT,
    history JSONB NOT NULL DEFAULT '[]',
    slots_summary JSONB NOT NULL DEFAULT '{}',
    last_message_at TIMESTAMPTZ,
    closing_message_sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_UPSERT_SQL = """
INSERT INTO conversations (
    thread_id, agent_slug, location_id, contact_id,
    contact_name, contact_phone, contact_email, channel,
    preferred_language, current_state, last_user_message, history,
    slots_summary, last_message_at, updated_at
)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
ON CONFLICT (thread_id) DO UPDATE SET
    -- COALESCE, not a blind overwrite: these four come straight off each
    -- inbound webhook's raw payload, which isn't guaranteed to carry the
    -- same fields on every turn (found live, 2026-09-29: a contact's
    -- email showed up on the first message of a conversation, then went
    -- NULL after the next turn -- GHL's own "message received" webhook
    -- doesn't always resend the full contact profile, apparently only
    -- the first event for a thread does). A later turn with less info
    -- must never erase what an earlier turn already established.
    -- current_state/last_user_message/history/slots_summary below stay a
    -- plain overwrite on purpose -- those come from the graph's own
    -- cumulative state (LangGraph's checkpointer), not the raw request,
    -- so they're always consistent turn to turn.
    contact_name = COALESCE(EXCLUDED.contact_name, conversations.contact_name),
    contact_phone = COALESCE(EXCLUDED.contact_phone, conversations.contact_phone),
    contact_email = COALESCE(EXCLUDED.contact_email, conversations.contact_email),
    channel = COALESCE(EXCLUDED.channel, conversations.channel),
    preferred_language = EXCLUDED.preferred_language,
    current_state = EXCLUDED.current_state,
    last_user_message = EXCLUDED.last_user_message,
    history = EXCLUDED.history,
    slots_summary = EXCLUDED.slots_summary,
    last_message_at = EXCLUDED.last_message_at,
    -- A real turn just happened, so the contact's own 24h WhatsApp window
    -- restarted from this new last_message_at -- whatever closing-message
    -- bookkeeping applied to the PREVIOUS window no longer means anything.
    closing_message_sent_at = NULL,
    updated_at = now();
"""

# A hand-picked allowlist of "business-meaningful" slots goes stale the
# moment a new subflow is authored (found live: an earlier version of this
# function only covered 3 classification fields and some of scheduling --
# missed value_ivf's `treat` (conventional/donor_eggs/ROPA -- the actual
# IVF-vs-ROPA distinction), objections' `obj`/`final_choice`,
# appointment_management, surrogacy_programs, and half of classification's
# own fields: prior treatment, embryo origin, first-contact flag...). It
# also wouldn't generalize across agents -- a list of family_aims_sam_text's
# fields means nothing for a future agent with its own vocabulary.
#
# `kind: captured` in the DSL YAML (vs. `control`/`retry_counter`) would be
# the authoritative, per-agent-correct signal, but that classification is
# compile-time-only -- it never reaches graph.json/RuntimeArtifact, so it
# isn't available here.
#
# Excluding mechanically instead, by the DSL's own naming conventions
# (matches `_CONSTANT_NAME_RE`/`*_try` already used the same way in
# agent_runtime's graph_builder.py): every compile-time constant
# (`AGENT_NAME`, `MAX_RETRY_ATTEMPTS`, ...) is UPPER_SNAKE_CASE, every
# retry counter ends in `_try`. Everything else captured, from any
# subflow of any agent, present or future, is business data by definition
# and included automatically -- no list to keep in sync per agent.
_CONSTANT_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_EXCLUDED_SLOT_NAMES = {
    "preferred_language",  # already its own column -- global slot, never namespaced (confirmed against a real graph.json: referenced as bare [preferred_language] everywhere, unlike available_slots below)
}
# Unlike preferred_language, available_slots is a per-subflow `capture:` slot
# like any other -- its real runtime key is namespaced per subflow instance
# (confirmed against family_aims_sam_text's real graph.json: the actual
# `capture` tuple is `["sc__available_slots", ...]` in scheduling,
# `["am__available_slots", ...]` in appointment_management, never the bare
# name). An exact-match exclusion on "available_slots" therefore never
# fired -- found auditing this function for a slots_summary example
# (2026-09-29): the raw appointment-slot dump this exclusion was written
# to keep out of `conversations` was landing in it anyway, on every agent
# with a scheduling subflow. Matched by suffix instead, same idea as the
# `_try` retry-counter check below.
_EXCLUDED_SLOT_SUFFIXES = ("available_slots",)


def build_slots_summary(slots: dict[str, Any] | None) -> dict[str, Any]:
    """Every captured slot, from any subflow of any agent, minus constants/retry counters/plumbing.

    Keeps each slot's real namespaced key as-is (e.g. `cl__nat`, `vi__treat`,
    `ob__obj`) rather than a prettified label -- multiple subflows reuse
    short names (`success`, `ok`, `appt`...) for unrelated things, and
    stripping the namespace prefix would silently collide them.
    """
    slots = slots or {}
    return {
        key: value
        for key, value in slots.items()
        if value is not None
        and key not in _EXCLUDED_SLOT_NAMES
        and not key.endswith("_try")
        and not any(key == suffix or key.endswith(f"__{suffix}") for suffix in _EXCLUDED_SLOT_SUFFIXES)
        and not _CONSTANT_NAME_RE.match(key)
    }


# WhatsApp's own customer-service window: Meta stops allowing a free-form
# reply once 24h have passed since the CONTACT's last message (ours don't
# reset it) -- after that you can only reach them again via a pre-approved
# template. 23:45 leaves a 15-minute safety margin against the sweep's own
# poll interval and any send latency, so the message goes out before the
# hard cutoff rather than racing it.
CLOSING_MESSAGE_INACTIVITY_THRESHOLD = timedelta(hours=23, minutes=45)


_pool_instance = None
_pool_lock = threading.Lock()


def _pool():
    """One shared connection pool, used by both the LangGraph checkpointer
    and every query in this module -- replaces a single long-lived
    connection reused across concurrently-running turns (each turn runs in
    its own threadpool thread via run_in_threadpool; a single psycopg
    connection isn't safe for that). `PostgresSaver` accepts a
    `ConnectionPool` directly (checked internally via `isinstance(conn,
    ConnectionPool)`), so nothing else needs its own connection strategy.

    `prepare_threshold=0` matches what `PostgresSaver.from_conn_string`
    itself uses -- needed for compatibility with a transaction-pooling
    proxy in front of the real Postgres (common on hosted providers like
    Supabase/Neon), harmless otherwise.

    Manually double-checked-locked instead of `@lru_cache`: found live
    (2026-09-29, real server, real Postgres) -- every compiled agent wired
    to GHL starts its own closing-message sweep loop at startup
    (`ghl_endpoint.py::_start_closing_sweep`), and with two agents both
    calling this function for the first time within the same instant, via
    separate `run_in_threadpool` threads, `lru_cache` doesn't stop both
    from entering the function body before either has finished -- it only
    dedupes *completed* calls. Each thread built its own real
    `ConnectionPool`, one got cached and kept, the other got garbage
    collected mid-open, logging (harmless, but noisy) `__del__` errors
    from its own background worker threads. A real lock serializes
    construction instead.
    """
    global _pool_instance
    if _pool_instance is not None:
        return _pool_instance
    with _pool_lock:
        if _pool_instance is not None:
            return _pool_instance
        from psycopg_pool import ConnectionPool

        max_size = int(os.environ.get("DATABASE_POOL_MAX_SIZE", "5"))
        pool = ConnectionPool(
            DATABASE_URL,
            min_size=1,
            max_size=max_size,
            open=True,
            kwargs={"autocommit": True, "prepare_threshold": 0, "connect_timeout": 5},
        )
        with pool.connection() as conn, conn.cursor() as cur:
            cur.execute(CONVERSATIONS_TABLE_SQL)
        _pool_instance = pool
        return _pool_instance


def build_postgres_checkpointer():
    """A `PostgresSaver` backed by the shared pool, tables created once via
    `.setup()`.

    Bug fixed here (2026-09-29, never caught because this was never run
    against a real Postgres until now): `PostgresSaver.from_conn_string(...)`
    is a `@contextmanager` -- calling it without `with` (as this function
    used to) returns a `_GeneratorContextManager`, not a `PostgresSaver`,
    so `.setup()` raised `AttributeError` on the very first real call.
    Constructing `PostgresSaver(pool)` directly sidesteps the contextmanager
    entirely and gets pooling for free.
    """
    from langgraph.checkpoint.postgres import PostgresSaver

    checkpointer = PostgresSaver(_pool())
    checkpointer.setup()
    return checkpointer


def upsert_conversation(
    *,
    thread_id: str,
    agent_slug: str,
    location_id: str,
    contact_id: str,
    contact: dict[str, Any] | None,
    slots: dict[str, Any] | None,
    current_state: str | None,
    last_user_message: str | None = None,
    history: list[str] | None = None,
    channel: str | None = None,
) -> None:
    """Best-effort mirror write -- never raises into the caller's turn.

    Called after a turn finishes, so it reflects the latest known slots
    (preferred_language, and the business fields in `slots_summary`),
    current_state, and recent conversation preview without needing its own
    copy of the graph's routing logic. Also the only place
    `closing_message_sent_at` gets cleared (see `_UPSERT_SQL`'s own
    comment) -- a real turn is exactly the event that starts a new 24h
    WhatsApp window.
    """
    if not postgres_enabled():
        return
    from psycopg.types.json import Jsonb

    contact = contact or {}
    slots = slots or {}
    try:
        with _pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                _UPSERT_SQL,
                (
                    thread_id,
                    agent_slug,
                    location_id,
                    contact_id,
                    contact.get("name"),
                    contact.get("phone"),
                    contact.get("email"),
                    channel,
                    slots.get("preferred_language"),
                    current_state,
                    last_user_message,
                    Jsonb(history or []),
                    Jsonb(build_slots_summary(slots)),
                    datetime.now(timezone.utc),
                ),
            )
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "[%s] Failed to upsert conversations row (non-fatal).", thread_id, exc_info=True
        )


def find_conversations_needing_closing_message(agent_slug: str, lookback_days: float) -> list[dict[str, Any]]:
    """Threads of `agent_slug` whose contact went quiet 23:45+ ago and
    haven't gotten their closing message for this window yet.

    `lookback_days` bounds the query from below too -- without it, a sweep
    that was down for a while (a redeploy, a Postgres outage) would catch
    back up by messaging contacts from long-dead conversations the moment
    it comes back online, which is exactly the kind of surprise a contact
    from weeks ago doesn't need. Per-agent because different agents may
    reasonably want a different tolerance (a slower-moving flow could
    justify a wider window) -- see `CompiledAgentGhlConfig.closing_message_lookback_days`
    in ghl_endpoint.py, one .env var per agent, same pattern as debounce.
    """
    if not postgres_enabled():
        return []
    from psycopg.rows import dict_row

    try:
        with _pool().connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                """
                SELECT thread_id, contact_id, location_id, channel, preferred_language
                FROM conversations
                WHERE agent_slug = %s
                  AND closing_message_sent_at IS NULL
                  AND last_message_at IS NOT NULL
                  AND last_message_at <= now() - %s::interval
                  AND last_message_at >= now() - %s::interval
                """,
                (agent_slug, CLOSING_MESSAGE_INACTIVITY_THRESHOLD, f"{lookback_days} days"),
            )
            return cur.fetchall()
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "[%s] Failed to query closing-message candidates (non-fatal).", agent_slug, exc_info=True
        )
        return []


def mark_closing_message_sent(thread_id: str) -> None:
    """Records that this thread got its closing message for the current
    24h window -- prevents `find_conversations_needing_closing_message`
    from picking it up again on the next sweep. Cleared back to NULL by
    `upsert_conversation` the next time this thread has a real turn (a new
    window starting from that new message)."""
    if not postgres_enabled():
        return
    try:
        with _pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE conversations SET closing_message_sent_at = now() WHERE thread_id = %s",
                (thread_id,),
            )
    except Exception:
        import logging

        logging.getLogger(__name__).warning(
            "[%s] Failed to mark closing message sent (non-fatal).", thread_id, exc_info=True
        )
