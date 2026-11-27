"""``CdnCache`` (e2e_support): a request whose page is gone is not a CDN failure."""

from __future__ import annotations

from typing import Any

from e2e_support import CdnCache, closed_target


class _Request:
    method = "GET"
    url = "https://cdn.jsdelivr.net/npm/x@1/+esm"


class _Route:
    def __init__(self, error: str) -> None:
        self.request = _Request()
        self.error = error
        self.calls: list[str] = []

    def fetch(self, timeout: float) -> Any:
        self.calls.append("fetch")
        raise RuntimeError(self.error)

    def abort(self) -> None:
        self.calls.append("abort")
        if "closed" in self.error:
            raise RuntimeError(self.error)


CLOSED = "Route.fetch: Target page, context or browser has been closed"


def test_closed_target_is_not_a_cdn_failure() -> None:
    cache = CdnCache()
    route = _Route(CLOSED)
    cache.handle(route)
    assert cache.failures == []
    assert route.calls == ["fetch", "abort"]  # no retries for a page that is gone


def test_network_errors_still_count() -> None:
    # negative control: a real network failure is retried, then recorded
    cache = CdnCache()
    route = _Route("net::ERR_CONNECTION_RESET")
    cache.handle(route)
    assert route.calls.count("fetch") == 3
    assert len(cache.failures) == 1 and "ERR_CONNECTION_RESET" in cache.failures[0]


def test_closed_target_markers() -> None:
    assert closed_target(RuntimeError(CLOSED))
    assert closed_target(RuntimeError("Target closed"))
    assert not closed_target(RuntimeError("net::ERR_NAME_NOT_RESOLVED"))
