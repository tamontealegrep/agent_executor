"""build_slots_summary is the mechanical (not hand-picked) filter that
decides what lands in Postgres's queryable `conversations.slots_summary`
column -- see postgres.py's own docstring for why a curated allowlist was
replaced with this exclusion rule (found live: an earlier hand-picked list
silently missed IVF/ROPA and other real business fields). These tests lock
in that exclusion rule so a future edit can't quietly narrow it again.
"""

from compiled_runner import postgres


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
        raise AssertionError("_connection() must not be called when postgres is disabled")

    monkeypatch.setattr(postgres, "_connection", _fail_if_called)

    postgres.upsert_conversation(
        thread_id="t1",
        agent_slug="family_aims_sam_text",
        location_id="loc",
        contact_id="contact",
        contact={"name": "Test"},
        slots={"vi__treat": "donor_eggs"},
        current_state="SC__SC_END",
    )
