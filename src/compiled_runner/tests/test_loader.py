"""Covers the two things loader.py hand-rolls instead of using a plain
@lru_cache: per-slug tool routing (_tools_base_url) and per-slug cache
invalidation (reload_compiled_agent) -- see loader.py's own comments for
why (the /admin/reload endpoint needs to rebuild one agent without
evicting every other agent's already-open checkpointer connection).

_build_compiled_agent is monkeypatched everywhere here: actually building
a graph needs a real graph.json plus a live OpenAI client, which is exactly
what these tests should NOT depend on to stay fast and offline.
"""

import pytest

from compiled_runner import loader


@pytest.fixture(autouse=True)
def _clear_agent_cache():
    """Every test starts from a clean cache -- module-level state would
    otherwise leak between tests (and between this file and any other
    test that happens to call load_compiled_agent for a real slug)."""
    loader._agent_cache.clear()
    yield
    loader._agent_cache.clear()


def test_tools_base_url_routes_family_aims_slugs(monkeypatch):
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8010")
    assert loader._tools_base_url("family_aims_sam_text") == "http://127.0.0.1:8010/family_aims/v1"
    assert loader._tools_base_url("family_aims_sam_en_voice") == "http://127.0.0.1:8010/family_aims/v1"


def test_tools_base_url_routes_babynova_slugs_to_novafem_surrogacy(monkeypatch):
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8010")
    assert (
        loader._tools_base_url("babynova_surrogate_questions_voice")
        == "http://127.0.0.1:8010/novafem_surrogacy/v1"
    )
    assert (
        loader._tools_base_url("babynova_triage_obstetrico_text")
        == "http://127.0.0.1:8010/novafem_surrogacy/v1"
    )


def test_tools_base_url_defaults_unmapped_slug_to_family_aims(monkeypatch):
    monkeypatch.setenv("HOST", "127.0.0.1")
    monkeypatch.setenv("PORT", "8010")
    assert loader._tools_base_url("some_future_agent") == "http://127.0.0.1:8010/family_aims/v1"


def test_tools_base_url_probes_localhost_when_host_is_0000(monkeypatch):
    monkeypatch.setenv("HOST", "0.0.0.0")
    monkeypatch.setenv("PORT", "9000")
    assert loader._tools_base_url("family_aims_sam_text") == "http://127.0.0.1:9000/family_aims/v1"


def test_load_compiled_agent_builds_once_and_caches(monkeypatch):
    calls = []

    def fake_build(slug):
        calls.append(slug)
        return (f"artifact-{slug}", f"graph-{slug}")

    monkeypatch.setattr(loader, "_build_compiled_agent", fake_build)

    first = loader.load_compiled_agent("family_aims_sam_text")
    second = loader.load_compiled_agent("family_aims_sam_text")

    assert first == ("artifact-family_aims_sam_text", "graph-family_aims_sam_text")
    assert first is second or first == second
    assert calls == ["family_aims_sam_text"]  # only built once


def test_load_compiled_agent_caches_independently_per_slug(monkeypatch):
    calls = []

    def fake_build(slug):
        calls.append(slug)
        return (f"artifact-{slug}", f"graph-{slug}")

    monkeypatch.setattr(loader, "_build_compiled_agent", fake_build)

    loader.load_compiled_agent("family_aims_sam_text")
    loader.load_compiled_agent("babynova_triage_obstetrico_text")
    loader.load_compiled_agent("family_aims_sam_text")

    assert calls == ["family_aims_sam_text", "babynova_triage_obstetrico_text"]


def test_reload_compiled_agent_rebuilds_only_that_slug(monkeypatch):
    calls = []

    def fake_build(slug):
        calls.append(slug)
        return (f"artifact-{slug}-{len(calls)}", f"graph-{slug}-{len(calls)}")

    monkeypatch.setattr(loader, "_build_compiled_agent", fake_build)

    loader.load_compiled_agent("family_aims_sam_text")
    loader.load_compiled_agent("babynova_triage_obstetrico_text")
    assert calls == ["family_aims_sam_text", "babynova_triage_obstetrico_text"]

    artifact = loader.reload_compiled_agent("family_aims_sam_text")

    assert artifact == "artifact-family_aims_sam_text-3"
    assert calls == ["family_aims_sam_text", "babynova_triage_obstetrico_text", "family_aims_sam_text"]

    # The untouched slug's cached tuple wasn't rebuilt.
    assert loader.load_compiled_agent("babynova_triage_obstetrico_text") == (
        "artifact-babynova_triage_obstetrico_text-2",
        "graph-babynova_triage_obstetrico_text-2",
    )
    # The reloaded slug now serves the freshly built tuple.
    assert loader.load_compiled_agent("family_aims_sam_text") == (
        "artifact-family_aims_sam_text-3",
        "graph-family_aims_sam_text-3",
    )


def test_load_compiled_agent_raises_file_not_found_for_unknown_slug():
    with pytest.raises(FileNotFoundError):
        loader.load_compiled_agent("this_slug_does_not_exist")
