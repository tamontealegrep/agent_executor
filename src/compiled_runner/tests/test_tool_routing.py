"""Covers how a contract's snake_case tool name reaches the kebab-case routes
the tools backend really exposes. Offline: an `httpx.MockTransport` stands in
for the backend.
"""

import httpx

from compiled_runner.tool_routing import ToolRoutingClient, candidate_urls

BASE = "http://127.0.0.1:8010"


def test_candidate_urls_kebab_cases_the_tool_name_then_falls_back_to_utils():
    assert candidate_urls(f"{BASE}/family_aims/v1/check_visa") == [
        f"{BASE}/family_aims/v1/check-visa",
        f"{BASE}/tools/v1/check-visa",
    ]


def test_candidate_urls_tries_the_babynova_suffix_first():
    assert candidate_urls(f"{BASE}/babynova/v1/book_appointment") == [
        f"{BASE}/babynova/v1/book-appointment-sur",
        f"{BASE}/babynova/v1/book-appointment",
        f"{BASE}/tools/v1/book-appointment",
    ]


def _client(routes: set[str], seen: list[str]) -> ToolRoutingClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path in routes:
            return httpx.Response(200, json={"ok": True})
        return httpx.Response(404, json={"detail": "Not Found"})

    return ToolRoutingClient(transport=httpx.MockTransport(handler))


def test_client_resolves_a_utils_tool_called_through_a_vertical_base():
    seen: list[str] = []
    response = _client({"/tools/v1/time-now"}, seen).post(f"{BASE}/family_aims/v1/time_now", json={})
    assert response.status_code == 200
    assert seen == ["/family_aims/v1/time-now", "/tools/v1/time-now"]


def test_client_remembers_the_resolved_url_so_later_calls_skip_the_misses():
    seen: list[str] = []
    client = _client({"/babynova/v1/book-appointment-sur"}, seen)
    client.post(f"{BASE}/babynova/v1/book_appointment", json={})
    seen.clear()
    client.post(f"{BASE}/babynova/v1/book_appointment", json={})
    assert seen == ["/babynova/v1/book-appointment-sur"]


def test_client_returns_the_first_candidates_404_when_nothing_matches():
    seen: list[str] = []
    response = _client(set(), seen).post(f"{BASE}/family_aims/v1/no_such_tool", json={})
    assert response.status_code == 404
    assert response.request.url.path == "/family_aims/v1/no-such-tool"
