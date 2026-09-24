from datetime import datetime, timezone

import pytest

from tools.utils.services.time_now import get_current_time


def test_get_current_time_returns_iso_string_with_offset():
    result = get_current_time("America/Bogota")
    assert result.endswith("-05:00")


def test_get_current_time_utc_matches_wall_clock_within_a_few_seconds():
    result = get_current_time("UTC")
    parsed = datetime.fromisoformat(result)
    assert abs((datetime.now(timezone.utc) - parsed).total_seconds()) < 5


def test_get_current_time_invalid_timezone_raises():
    with pytest.raises(Exception):
        get_current_time("No/Existe")
