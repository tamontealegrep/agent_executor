"""normalize_language backs the fix for a real bug (2026-09-29): GHL's
contact_language/customData.language were never mapped into contact.language,
so opening.yaml's "Infer [preferred_language] from {{contact.language}}
first" always had nothing to read -- see ghl_request.py's own comment on
the fix. These tests lock in every alias found live plus the ones a CRM
could plausibly send, so a future edit can't quietly narrow the list.
"""

import pytest

from instances.helpers.text import normalize_language


@pytest.mark.parametrize(
    "raw",
    ["en", "EN", "English", "ENGLISH", "eng", "Ingles", "ingles", "en-US", "en_GB"],
)
def test_normalize_language_recognizes_english_variants(raw):
    assert normalize_language(raw) == "en"


@pytest.mark.parametrize(
    "raw",
    ["es", "ES", "Spanish", "spanish", "esp", "Español", "espanol", "Castellano", "es-CO", "es_MX"],
)
def test_normalize_language_recognizes_spanish_variants(raw):
    assert normalize_language(raw) == "es"


@pytest.mark.parametrize(
    "raw",
    ["pt", "PT", "Portuguese", "portuguese", "por", "Português", "portugues", "pt-BR", "pt_PT"],
)
def test_normalize_language_recognizes_portuguese_variants(raw):
    assert normalize_language(raw) == "pt"


@pytest.mark.parametrize("raw", ["French", "fr", "de", "xx", "not a language"])
def test_normalize_language_defaults_to_english_for_unsupported_languages(raw):
    """A value was actually stated, just not one of the 3 supported
    languages -- default to English rather than leave the conversation
    with no language signal at all."""
    assert normalize_language(raw) == "en"


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_normalize_language_returns_none_for_empty_input(raw):
    """No value at all is different from an unsupported one -- a caller
    with its own fallback (opening.yaml infers from the user's first
    message when [contact.language] is missing) needs to know nothing was
    said, not silently get handed the English default."""
    assert normalize_language(raw) is None
