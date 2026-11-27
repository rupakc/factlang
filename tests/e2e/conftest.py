"""Browser end-to-end tests of the built site (site spec 6): real Chromium, real CDNs.

They run only when ``FL_SITE_URL`` names a served build of the site (``mkdocs build`` output
behind any static HTTP server, or the published site); otherwise every test here is skipped.
Python Playwright is needed then (``pip install -e ".[e2e]"`` and
``python -m playwright install chromium``); it is imported lazily, so the rest of the suite
never needs it. How to run them locally: ``CONTRIBUTING.md``.

Two things the fixtures do on the way, both confined to the browser the tests drive:

* **CDN retries.** Requests to the playground's CDNs (``cdn.jsdelivr.net``,
  ``extensions.duckdb.org``) go through a route that retries a failed or 5xx fetch at once
  (at most 3 attempts in 120 s, no sleep: a sleep would stall the sync Playwright session)
  and keeps the bytes for the rest of the session, so a flaky CDN neither fails the run nor
  is downloaded again by every test. If a CDN stays down, the tests fail (and the
  publish they gate waits).
* **Sitemap.** Material's instant navigation only intercepts URLs listed in ``sitemap.xml``,
  which names the production ``site_url``. Served from another origin, the sitemap is
  rewritten in flight to that origin, so instant navigation behaves as it does in production.
  The built files are never modified.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from e2e_support import CDN_HOSTS, INIT_SCRIPT, SITE_URL, CdnCache, Seen, production_site_url


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    here = Path(__file__).resolve().parent
    skip = pytest.mark.skip(reason="set FL_SITE_URL to a served build of the site to run")
    for item in items:
        if Path(str(item.fspath)).resolve().is_relative_to(here):
            item.add_marker(pytest.mark.e2e)
            if not SITE_URL:
                item.add_marker(skip)


@pytest.fixture(scope="session")
def site_url() -> str:
    return SITE_URL if SITE_URL.endswith("/") else SITE_URL + "/"


@pytest.fixture(scope="session")
def cdn() -> CdnCache:
    return CdnCache()


@pytest.fixture(scope="session")
def playwright_api() -> Iterator[Any]:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as api:
        yield api


@pytest.fixture(scope="session")
def browser(playwright_api: Any) -> Iterator[Any]:
    chromium = playwright_api.chromium.launch()
    yield chromium
    chromium.close()


def _sitemap_route(site: str) -> Callable[[Any], None]:
    production = production_site_url()

    def handle(route: Any) -> None:
        response = route.fetch()
        text = response.text()
        if production and production != site:
            text = text.replace(production, site)
        route.fulfill(response=response, body=text)

    return handle


@pytest.fixture
def make_context(
    browser: Any, site_url: str, cdn: CdnCache
) -> Iterator[Callable[..., tuple[Any, Seen]]]:
    """``make_context(**options) -> (context, seen)``: an instrumented browser context."""
    made: list[Any] = []

    def make(engine: Any = None, **options: Any) -> tuple[Any, Seen]:
        options.setdefault("viewport", {"width": 1440, "height": 900})
        context = (engine or browser).new_context(**options)
        made.append(context)
        seen = Seen()
        context.add_init_script(INIT_SCRIPT)
        for host in CDN_HOSTS:
            context.route(f"https://{host}/**", cdn.handle)
        context.route(site_url + "sitemap.xml", _sitemap_route(site_url))
        context.on("request", lambda r: seen.requests.append(r))
        context.on("response", lambda r: seen.responses.append(r))

        def on_page(page: Any) -> None:
            page.on("pageerror", lambda exc: seen.page_errors.append(str(exc)))
            page.on("console", lambda m: seen.console.append(f"{m.type}: {m.text[:300]}"))

            def on_dialog(dialog: Any) -> None:
                seen.dialogs.append(dialog.message)
                dialog.dismiss()

            page.on("dialog", on_dialog)

        context.on("page", on_page)
        return context, seen

    yield make
    for context in made:
        with contextlib.suppress(Exception):  # its browser was closed first (WebKit test)
            context.close()
    failures, cdn.failures = cdn.failures, []
    assert failures == [], "the CDN stayed down: " + "; ".join(failures)
