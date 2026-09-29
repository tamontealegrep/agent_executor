"""build_slots_summary is the mechanical (not hand-picked) filter that
decides what lands in Postgres's queryable `conversations.slots_summary`
column -- see postgres.py's own docstring for why a curated allowlist was
replaced with this exclusion rule (found live: an earlier hand-picked list
silently missed IVF/ROPA and other real business fields). These tests lock
in that exclusion rule so a future edit can't quietly narrow it again.
"""

import time

import pytest

from compiled_runner import postgres


# --- _build_database_url (component env vars vs. a single DATABASE_URL) ----


def _clear_database_env(monkeypatch):
    for var in ("DATABASE_URL", "DATABASE_HOST", "DATABASE_PORT", "DATABASE_USER", "DATABASE_PASSWORD", "DATABASE_NAME", "DATABASE_REGION"):
        monkeypatch.delenv(var, raising=False)


def test_build_database_url_falls_back_to_database_url_when_no_host_set(monkeypatch):
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgresql://someone:pw@example.com:5432/db")
    assert postgres._build_database_url() == "postgresql://someone:pw@example.com:5432/db"


def test_build_database_url_from_components_without_region_is_internal_style(monkeypatch):
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("DATABASE_HOST", "dpg-d81lvig3kofs73a639dg-a")
    monkeypatch.setenv("DATABASE_USER", "admin")
    monkeypatch.setenv("DATABASE_PASSWORD", "secret123")
    monkeypatch.setenv("DATABASE_NAME", "GHL_AGENTS")
    assert postgres._build_database_url() == "postgresql://admin:secret123@dpg-d81lvig3kofs73a639dg-a:5432/GHL_AGENTS"


def test_build_database_url_from_components_with_region_is_external_style(monkeypatch):
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("DATABASE_HOST", "dpg-d81lvig3kofs73a639dg-a")
    monkeypatch.setenv("DATABASE_REGION", "oregon-postgres.render.com")
    monkeypatch.setenv("DATABASE_USER", "admin")
    monkeypatch.setenv("DATABASE_PASSWORD", "secret123")
    monkeypatch.setenv("DATABASE_NAME", "GHL_AGENTS")
    assert postgres._build_database_url() == (
        "postgresql://admin:secret123@dpg-d81lvig3kofs73a639dg-a.oregon-postgres.render.com:5432/GHL_AGENTS"
    )


def test_build_database_url_respects_custom_port(monkeypatch):
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("DATABASE_HOST", "myhost")
    monkeypatch.setenv("DATABASE_PORT", "6543")
    monkeypatch.setenv("DATABASE_USER", "u")
    monkeypatch.setenv("DATABASE_PASSWORD", "p")
    monkeypatch.setenv("DATABASE_NAME", "n")
    assert postgres._build_database_url() == "postgresql://u:p@myhost:6543/n"


def test_build_database_url_url_encodes_special_characters_in_password(monkeypatch):
    """A hand-assembled connection string breaks on a password containing
    @, :, /, etc. -- this is exactly the gotcha component-based config was
    supposed to sidestep, so it must actually be encoded, not just
    concatenated."""
    _clear_database_env(monkeypatch)
    monkeypatch.setenv("DATABASE_HOST", "myhost")
    monkeypatch.setenv("DATABASE_USER", "admin")
    monkeypatch.setenv("DATABASE_PASSWORD", "p@ss/word:with#special?chars")
    monkeypatch.setenv("DATABASE_NAME", "n")

    url = postgres._build_database_url()

    from urllib.parse import unquote, urlparse

    parsed = urlparse(url)
    assert unquote(parsed.password) == "p@ss/word:with#special?chars"
    assert unquote(parsed.username) == "admin"
    assert parsed.hostname == "myhost"


def test_keeps_real_captured_business_data():
    slots = {"cl__nat": "colombiana", "vi__treat": "donor_eggs", "ob__obj": "cost"}
    assert postgres.build_slots_summary(slots) == slots


def test_drops_upper_snake_case_constants():
    slots = {"vi__treat": "conventional", "AGENT_NAME": "Sam", "MAX_RETRY_ATTEMPTS": "3"}
    assert postgres.build_slots_summary(slots) == {"vi__treat": "conventional"}


def test_drops_retry_counter_suffix():
    slots = {"vi__treat": "conventional", "document_retry_count": 0}
    # document_retry_count doesn't end in _try, so it's kept -- only the
    # _try suffix (the DSL's actual retry-counter convention) is excluded.
    assert postgres.build_slots_summary(slots) == slots

    slots_with_try = {"vi__treat": "conventional", "end_try": 2}
    assert postgres.build_slots_summary(slots_with_try) == {"vi__treat": "conventional"}


def test_drops_explicitly_excluded_plumbing_fields():
    slots = {
        "vi__treat": "donor_eggs",
        "preferred_language": "ES",
        "available_slots": [{"start_co": "2026-01-01T09:00:00-05:00"}],
    }
    assert postgres.build_slots_summary(slots) == {"vi__treat": "donor_eggs"}


def test_drops_namespaced_available_slots():
    """available_slots is a per-subflow `capture:` slot like any other --
    its real runtime key is namespaced (sc__available_slots in scheduling,
    am__available_slots in appointment_management, confirmed against a
    real graph.json), never the bare name the exclusion originally only
    matched exactly."""
    slots = {
        "vi__treat": "donor_eggs",
        "sc__available_slots": [{"start_co": "2026-01-01T09:00:00-05:00"}],
        "am__available_slots": [{"start_co": "2026-01-01T10:00:00-05:00"}],
    }
    assert postgres.build_slots_summary(slots) == {"vi__treat": "donor_eggs"}


def test_drops_none_values():
    slots = {"vi__treat": "donor_eggs", "ob__obj": None}
    assert postgres.build_slots_summary(slots) == {"vi__treat": "donor_eggs"}


def test_empty_or_none_input_returns_empty_dict():
    assert postgres.build_slots_summary(None) == {}
    assert postgres.build_slots_summary({}) == {}


def test_postgres_enabled_reflects_database_url(monkeypatch):
    monkeypatch.setattr(postgres, "DATABASE_URL", "")
    assert postgres.postgres_enabled() is False

    monkeypatch.setattr(postgres, "DATABASE_URL", "postgresql://user:pass@host/db")
    assert postgres.postgres_enabled() is True


def test_upsert_conversation_is_a_noop_without_postgres(monkeypatch):
    """No DATABASE_URL -> never touches the DB, never raises."""
    monkeypatch.setattr(postgres, "DATABASE_URL", "")

    def _fail_if_called():
        raise AssertionError("_pool() must not be called when postgres is disabled")

    monkeypatch.setattr(postgres, "_pool", _fail_if_called)

    postgres.upsert_conversation(
        thread_id="t1",
        agent_slug="family_aims_sam_text",
        location_id="loc",
        contact_id="contact",
        contact={"name": "Test"},
        slots={"vi__treat": "donor_eggs"},
        current_state="SC__SC_END",
    )


def test_find_conversations_needing_closing_message_is_a_noop_without_postgres(monkeypatch):
    monkeypatch.setattr(postgres, "DATABASE_URL", "")

    def _fail_if_called():
        raise AssertionError("_pool() must not be called when postgres is disabled")

    monkeypatch.setattr(postgres, "_pool", _fail_if_called)

    assert postgres.find_conversations_needing_closing_message("family_aims_sam_text", 3.0) == []


def test_mark_closing_message_sent_is_a_noop_without_postgres(monkeypatch):
    monkeypatch.setattr(postgres, "DATABASE_URL", "")

    def _fail_if_called():
        raise AssertionError("_pool() must not be called when postgres is disabled")

    monkeypatch.setattr(postgres, "_pool", _fail_if_called)

    postgres.mark_closing_message_sent("family_aims_sam_text:loc:contact")  # must not raise


def test_closing_message_inactivity_threshold_is_23h45m():
    """The 15-minute safety margin before WhatsApp's hard 24h cutoff is the
    whole point of this feature -- pin the exact value."""
    assert postgres.CLOSING_MESSAGE_INACTIVITY_THRESHOLD.total_seconds() == 23 * 3600 + 45 * 60


def test_pool_construction_is_single_flight_under_concurrent_first_access(monkeypatch):
    """Found live (2026-09-29, real server + real Postgres): two agents'
    closing-message sweep loops both call _pool() for the first time at
    startup, from separate run_in_threadpool threads, at nearly the same
    instant. @lru_cache alone doesn't stop both from entering the function
    body before either finishes -- it only dedupes completed calls, so
    both built a real ConnectionPool and one got discarded mid-open
    (harmless but noisy __del__ errors). _pool_lock must serialize this."""
    import threading as _threading

    class _FakeCursor:
        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def execute(self, *args, **kwargs):
            pass

    class _FakeConn:
        def cursor(self, *args, **kwargs):
            return _FakeCursor()

    class _FakeConnCtx:
        def __enter__(self):
            return _FakeConn()

        def __exit__(self, *exc_info):
            return False

    class _FakeConnectionPool:
        construction_count = 0

        def __init__(self, *args, **kwargs):
            type(self).construction_count += 1
            time.sleep(0.05)  # widen the race window a slow real construction would have

        def connection(self):
            return _FakeConnCtx()

    monkeypatch.setattr(postgres, "DATABASE_URL", "postgresql://fake:fake@localhost/fake")
    monkeypatch.setattr(postgres, "_pool_instance", None)
    monkeypatch.setattr("psycopg_pool.ConnectionPool", _FakeConnectionPool)

    threads = [_threading.Thread(target=postgres._pool) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert _FakeConnectionPool.construction_count == 1


@pytest.mark.skipif(not postgres.postgres_enabled(), reason="needs a real DATABASE_URL (e.g. local Docker Postgres)")
def test_upsert_conversation_does_not_let_a_leaner_turn_erase_known_contact_fields():
    """Found live (2026-09-29): a contact's email was present on the first
    inbound webhook of a conversation, then came back NULL after the next
    turn -- GHL's own "message received" event doesn't always resend the
    full contact profile (apparently only the very first event for a
    thread does). Before this fix, contact_name/contact_phone/contact_email/
    channel were a blind overwrite on every turn, so a later, leaner
    payload silently erased what an earlier one had already established.
    Only runs against a real Postgres (COALESCE-in-ON-CONFLICT behavior
    can't be verified through a mock)."""
    thread_id = "_coalesce_test:loc:contact"
    try:
        postgres.upsert_conversation(
            thread_id=thread_id,
            agent_slug="family_aims_sam_text",
            location_id="loc",
            contact_id="contact",
            contact={"name": "Tomas Montealegre", "phone": "+573215616921", "email": "montealegre.tomas@outlook.com"},
            slots={"preferred_language": "es"},
            current_state="O__OP_GREET",
            channel="WHATSAPP",
        )
        # A leaner second turn -- no email key at all, like the real payload found live.
        postgres.upsert_conversation(
            thread_id=thread_id,
            agent_slug="family_aims_sam_text",
            location_id="loc",
            contact_id="contact",
            contact={"name": "Tomas Montealegre", "phone": "+573215616921"},
            slots={"preferred_language": "es", "svc": "surrogacy"},
            current_state="CL__CL_DEC_SVC",
            channel="WHATSAPP",
        )

        with postgres._pool().connection() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT contact_name, contact_email, current_state FROM conversations WHERE thread_id = %s",
                (thread_id,),
            )
            name, email, current_state = cur.fetchone()

        assert email == "montealegre.tomas@outlook.com"  # survived the leaner turn
        assert name == "Tomas Montealegre"
        assert current_state == "CL__CL_DEC_SVC"  # still updates normally
    finally:
        with postgres._pool().connection() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM conversations WHERE thread_id = %s", (thread_id,))
