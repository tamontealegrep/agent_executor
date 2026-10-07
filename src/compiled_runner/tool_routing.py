"""Resolves a contract's tool name to the endpoint the tools backend really exposes.

The engine POSTs to `{base_url}/{tool.name}` (snake_case, straight from the
contract). The backend only exposes kebab-case routes, and not all of them
under the agent's own vertical:

  - `check_visa` -> `/family_aims/v1/check-visa`
  - every babynova route carries a `-sur` suffix:
    `book_appointment` -> `/babynova/v1/book-appointment-sur`
  - shared utils live only under `/tools/v1`: `time_now` -> `/tools/v1/time-now`
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

UTILS_PREFIX = "/tools/v1"

# Shared tools mounted only under UTILS_PREFIX (tools.gateway's api_router), so
# asking the agent's own vertical first would just burn a 404 on every call.
_UTILS_TOOLS = frozenset(
    {"time-now", "callback", "check-days-elapsed", "calculate-bmi", "update-custom-field", "echo", "pause"}
)

# App prefix (first path segment) -> suffix every one of its routes carries.
_ROUTE_SUFFIX_BY_APP = {"babynova": "-sur"}


def candidate_urls(url: str) -> list[str]:
    """Ordered URLs to try for a `{base}/{tool_name}` request, most likely first."""
    parts = urlsplit(url)
    head, _, tail = parts.path.rpartition("/")
    kebab = tail.replace("_", "-")
    suffix = _ROUTE_SUFFIX_BY_APP.get(head.strip("/").split("/")[0])

    paths = [f"{head}/{kebab}"]
    if suffix and not kebab.endswith(suffix):
        paths.insert(0, f"{head}/{kebab}{suffix}")
    if kebab in _UTILS_TOOLS:
        paths.insert(0, f"{UTILS_PREFIX}/{kebab}")
    else:
        paths.append(f"{UTILS_PREFIX}/{kebab}")

    unique_paths = list(dict.fromkeys(paths))
    return [urlunsplit((parts.scheme, parts.netloc, p, parts.query, parts.fragment)) for p in unique_paths]


class ToolRoutingClient(httpx.Client):
    """`httpx.Client` that retries a 404 tool POST on the other candidate URLs.

    Pass it as `build_graph(tool_http_client=...)`. The first URL that does
    not answer 404 is remembered per requested URL, so only a tool's first
    call can pay for the extra requests. When every candidate answers 404 the
    first candidate's response is returned, so a genuine application 404
    keeps its own error body.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._resolved: dict[str, str] = {}

    def post(self, url: Any, *args: Any, **kwargs: Any) -> httpx.Response:  # type: ignore[override]
        requested = str(url)
        candidates = [self._resolved[requested]] if requested in self._resolved else candidate_urls(requested)

        first_response: httpx.Response | None = None
        for candidate in candidates:
            response = super().post(candidate, *args, **kwargs)
            if response.status_code != 404:
                self._resolved[requested] = candidate
                return response
            first_response = first_response or response
        return first_response  # type: ignore[return-value]
