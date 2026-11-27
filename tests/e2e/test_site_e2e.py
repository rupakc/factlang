"""The built site in a real browser (site spec 6, items 1-6 and the landing budget).

Skipped unless ``FL_SITE_URL`` names a served build (see ``conftest.py`` and
``CONTRIBUTING.md``). Every expected verdict is computed here, in Python, by ``factlang`` on
DuckDB (``duckdb==1.4.3``, the engine DuckDB-WASM 1.32.0 embeds) over the same data, so the
browser has to agree with the library, not with a number written into this file.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest
from e2e_support import BOOT_MS, Seen

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "playground" / "fixtures"
ENGINE_JS = ROOT / "site-playground" / "engine.js"

# The hosts the playground may contact besides the site itself (spec 3, amended for ICU).
ENGINE_HOSTS = {"cdn.jsdelivr.net", "extensions.duckdb.org"}
# The MkDocs Material theme's own requests on every page, playground or not: the web fonts
# (mkdocs.yml theme.font) and the repository widget (repo_url). The playground adds none.
SITE_CHROME_HOSTS = {"fonts.googleapis.com", "fonts.gstatic.com", "api.github.com"}
# What only the playground may fetch: Pyodide, DuckDB-WASM, ICU, the wheel and the glue.
ENGINE_URL = re.compile(
    r"/pyodide/|@duckdb/|apache-arrow|extensions\.duckdb\.org|\.whl\b|wheel\.json|"
    r"/assets/playground/(?!playground\.(?:js|css)(?:\?|$))"
)
LANDING_BUDGET = 1_500_000  # raw bytes, web fonts excluded (spec 6)

HOSTILE_HEADER = "<img src=x onerror=alert(1)>"
HOSTILE_FILE = "<img src=x onerror=alert(2)> data.csv"
CSV_SECRET = "fl-csv-secret-5d1e"  # appears only inside hostile.csv

Context = Callable[..., tuple[Any, Seen]]


# -- Python-side expectations -------------------------------------------------------------------


def _glue() -> ModuleType:
    name = "factlang_playground_duckdb_run"
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, FIXTURES / "duckdb_run.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    glue: ModuleType = module.load_glue()
    return glue


def expected(text: str, csv: dict[str, Path] | None = None) -> list[tuple[str, str]]:
    """``(verdict, explanation)`` per claim: the library on DuckDB over the playground's data."""
    import duckdb

    import factlang as fl

    con = duckdb.connect()
    con.execute("SET GLOBAL TimeZone = 'UTC'")  # the playground's session zone
    for statement in _glue().SAMPLE_SQL:
        con.execute(statement)
    for table, path in (csv or {}).items():
        quoted = '"' + table.replace('"', '""') + '"'
        literal = "'" + str(path).replace("'", "''") + "'"
        con.execute(f"CREATE TABLE {quoted} AS SELECT * FROM read_csv_auto({literal})")
    report = fl.Checker(fl.DBAPIConnector(con, "duckdb")).check(text)
    return [(o.verdict.value, o.explanation or "") for o in report.outcomes]


def default_example() -> str:
    text: str = json.loads(_glue().SAMPLE_EXAMPLES_JSON)[0]["text"]
    return text


def table_names(files: list[str]) -> list[str]:
    glue = _glue()
    existing = ["customers", "orders"]
    names = []
    for name in files:
        table = glue.table_name_for(name, existing)
        existing.append(table)
        names.append(table)
    return names


# -- driving the page ---------------------------------------------------------------------------


def open_playground(make_context: Context, site_url: str, query: str = "", **kw: Any) -> Any:
    routes = kw.pop("routes", ())
    context, seen = make_context(**kw)
    page = context.new_page()
    for pattern, handler in routes:
        page.route(pattern, handler)
    page.goto(site_url + "playground/" + query)
    try:
        page.wait_for_selector("#fl-pg-engine.is-ready", timeout=BOOT_MS)
    except Exception:
        state = page.evaluate(
            "() => [...document.querySelectorAll('#fl-pg-status, #fl-pg-engine,"
            " #fl-pg-engine-status')].map(e => e.id + ': ' + e.textContent)"
        )
        pending = [r.url for r in seen.requests if r.response() is None and not r.failure]
        failed = [(r.url, r.failure) for r in seen.requests if r.failure]
        pytest.fail(
            f"the playground did not start: {state}; page errors {seen.page_errors}; "
            f"console {seen.console[-10:]}; failed {failed}; unanswered {pending[:10]}"
        )
    return page, seen


def wait_idle_verdicts(page: Any) -> None:
    page.wait_for_function(
        "() => { const b = document.querySelector('[data-action=check]');"
        " return b && !b.disabled && document.querySelector('.fl-pg__verdicts'); }",
        timeout=BOOT_MS,
    )


def run_check(page: Any, text: str | None = None) -> list[tuple[str, str]]:
    if text is not None:
        page.fill("#fl-pg-source", text)
    page.click("[data-action=check]")
    wait_idle_verdicts(page)
    return cards(page)


def cards(page: Any) -> list[tuple[str, str]]:
    found: list[list[str]] = page.eval_on_selector_all(
        ".fl-pg__verdicts .fl-pg__card",
        """els => els.map(e => [
             [...e.classList].find(c => c.startsWith('fl-pg__card--')).slice(13),
             (e.querySelector('.fl-pg__why') || {textContent: ''}).textContent])""",
    )
    return [(v, why) for v, why in found]


def load_csv(page: Any, files: list[tuple[str, bytes]], tables: list[str]) -> None:
    payload = [{"name": n, "mimeType": "text/csv", "buffer": b} for n, b in files]
    page.set_input_files("#fl-pg-csv", payload)
    for table in tables:
        page.wait_for_selector(f'.fl-pg__table[data-table="{table}"]', timeout=120_000)
    page.wait_for_function("() => !document.querySelector('#fl-pg-load').disabled", timeout=120_000)


def table_list(page: Any) -> list[str]:
    tables: list[str] = page.eval_on_selector_all(
        ".fl-pg__table", "e => e.map(x => x.dataset.table)"
    )
    return tables


def assert_csv_never_sent(seen: Seen, *needles: bytes) -> None:
    """No request carries file contents: every request is a GET or HEAD without a body, and
    no URL holds any of ``needles``."""
    for request in seen.requests:
        assert request.method in ("GET", "HEAD"), (request.method, request.url)
        body = request.post_data_buffer
        assert not body, request.url
        url = unquote(request.url).encode("utf-8", "replace")
        for needle in needles:
            assert needle not in url, request.url


def engine_status(page: Any) -> str:
    text: str = page.inner_text("#fl-pg-engine-status")
    return text


def worker_counts(page: Any) -> dict[str, int]:
    counts: dict[str, int] = page.evaluate(
        "() => ({blob: window.__fl.blobWorkers, pyodide: window.__fl.pyodideLoads})"
    )
    return counts


# -- (1) and the landing budget: pages other than the playground stay light ---------------------


def _page_requests(make_context: Context, url: str) -> tuple[Any, Seen]:
    context, seen = make_context()
    page = context.new_page()
    page.goto(url, wait_until="networkidle")
    page.wait_for_timeout(1000)
    return page, seen


def _engine_requests(seen: Seen) -> list[str]:
    return [u for u in seen.urls() if ENGINE_URL.search(u)]


def test_landing_fetches_no_engine_and_stays_under_budget(
    make_context: Context, site_url: str
) -> None:
    page, seen = _page_requests(make_context, site_url)
    assert _engine_requests(seen) == []
    assert "cdn.jsdelivr.net" not in seen.hosts()
    assert worker_counts(page) == {"blob": 0, "pyodide": 0}
    assert any(u.endswith("assets/playground/playground.js") for u in seen.urls())

    total = 0
    for response in seen.responses:
        request = response.request
        if request.resource_type == "font" or "fonts.googleapis.com" in request.url:
            continue
        try:
            total += len(response.body())
        except Exception:  # a worker script or redirect has no readable body here
            total += response.request.sizes()["responseBodySize"]
    assert 0 < total < LANDING_BUDGET, total
    assert seen.page_errors == []


def test_guide_page_fetches_no_engine(make_context: Context, site_url: str) -> None:
    page, seen = _page_requests(make_context, site_url + "guide/verdicts/")
    assert _engine_requests(seen) == []
    assert worker_counts(page) == {"blob": 0, "pyodide": 0}
    local = urlsplit(site_url).hostname
    assert seen.hosts() - {local} <= SITE_CHROME_HOSTS
    assert seen.page_errors == []


# -- (2) the default example, and the hosts the playground contacts ------------------------------


def test_engine_allowed_hosts_are_the_spec_list() -> None:
    found = re.search(r"ALLOWED_HOSTS = Object\.freeze\(\[([^\]]*)\]\)", ENGINE_JS.read_text())
    assert found is not None
    assert set(re.findall(r'"([^"]+)"', found.group(1))) == ENGINE_HOSTS


def test_default_example_gives_the_library_verdicts(make_context: Context, site_url: str) -> None:
    page, seen = open_playground(make_context, site_url)
    wait_idle_verdicts(page)  # the default example checks itself once DuckDB is up
    assert page.input_value("#fl-pg-source") == default_example()
    want = expected(default_example())
    # the landing ledger: the JOIN and VALUES claims fail, the AVG has no rows to decide on
    assert [v for v, _ in want] == ["fail", "pass", "fail", "pass", "pass", "no_data"]
    assert cards(page) == want
    assert "v1.4.3" in page.inner_text("#fl-pg-engine")

    # Only the site itself, the engine's CDNs and the theme's own hosts; and the playground
    # adds nothing beyond the engine's list to what any other page contacts.
    local = urlsplit(site_url).hostname
    hosts = seen.hosts() - {local}
    assert {"cdn.jsdelivr.net"} <= hosts
    assert hosts - SITE_CHROME_HOSTS <= ENGINE_HOSTS
    other = [u for u in seen.urls() if urlsplit(u).scheme not in ("http", "https", "blob", "data")]
    assert other == []
    assert seen.page_errors == []
    assert_csv_never_sent(seen)


# -- values crossing the Arrow decoder ---------------------------------------------------------


def test_arrow_decoder_keeps_decimal_hugeint_and_microseconds(
    make_context: Context, site_url: str
) -> None:
    page, seen = open_playground(make_context, site_url)
    wait_idle_verdicts(page)
    path = FIXTURES / "big_types.csv"
    load_csv(page, [(path.name, path.read_bytes())], ["big_types"])

    import duckdb

    con = duckdb.connect()
    for statement in _glue().SAMPLE_SQL:
        con.execute(statement)
    (total,) = con.execute("SELECT SUM(amount) FROM orders").fetchone()
    (paid,) = con.execute("SELECT SUM(amount) FROM orders WHERE status = 'paid'").fetchone()
    text = "\n".join(
        [
            # DECIMAL(10,2) sums: a scaled integer (x100) would fail these
            f"SUM orders.amount VALUE =={total}",
            f"SUM orders.amount VALUE =={paid} WHERE status = 'paid'",
            "MIN orders.amount VALUE ==5",
            # SUM of BIGINT is HUGEINT: past 2^63, exact to the last digit (a double is not)
            "SUM big_types.v VALUE ==12000000000000000002",
            "SUM big_types.v VALUE ==12000000000000000001",
            # microsecond TIMESTAMPs: milliseconds would make the first two fail
            "MAX big_types.ts VALUE >TIMESTAMP '2026-01-01T00:00:00'",
            "MAX big_types.ts VALUE ==TIMESTAMP '2026-01-01T00:00:00.000001'",
            "MIN big_types.ts VALUE <TIMESTAMP '2025-06-01T12:00:00.000001'",
        ]
    )
    want = expected(text, {"big_types": path})
    assert [v for v, _ in want] == ["pass", "pass", "pass", "pass", "fail", "pass", "pass", "pass"]
    assert run_check(page, text) == want
    assert seen.page_errors == []
    assert_csv_never_sent(seen, b"6000000000000000001", b"23:59:59.999999")


# -- ICU unavailable ----------------------------------------------------------------------------


def test_icu_blocked_errors_fresh_and_keeps_the_rest(make_context: Context, site_url: str) -> None:
    page, seen = open_playground(
        make_context,
        site_url,
        routes=[("https://extensions.duckdb.org/**", lambda route: route.abort())],
    )
    wait_idle_verdicts(page)
    assert "ICU" in engine_status(page)
    text = "FRESH orders.shipped_at WITHIN 30d\nUNIQUE customers.id\nROWS customers VALUE ==500\n"
    got = run_check(page, text)
    assert got[0][0] == "error"
    assert "ICU extension" in got[0][1] and "extensions.duckdb.org" in got[0][1]
    assert got[1:] == expected(text)[1:] == [("pass", got[1][1]), ("pass", got[2][1])]
    assert any("extensions.duckdb.org" in u for u in seen.urls())  # it was tried, and blocked


# -- (3) and (4): CSV files, hostile names, nothing uploaded -------------------------------------


def test_csv_files_hostile_names_are_inert_and_never_sent(
    make_context: Context, site_url: str
) -> None:
    page, seen = open_playground(make_context, site_url)
    wait_idle_verdicts(page)
    hostile = (FIXTURES / "hostile.csv").read_bytes()
    quoted = (FIXTURES / "quoted_names.csv").read_bytes()
    malformed = (FIXTURES / "malformed.csv").read_bytes()
    t_hostile, t_quoted, _ = table_names([HOSTILE_FILE, "quoted_names.csv", "malformed.csv"])
    load_csv(
        page,
        [(HOSTILE_FILE, hostile), ("quoted_names.csv", quoted), ("malformed.csv", malformed)],
        [t_hostile, t_quoted],
    )
    # the malformed file is refused with DuckDB's reason; everything else is loaded
    assert table_list(page) == ["customers", "orders", t_hostile, t_quoted]
    errors = page.eval_on_selector_all(".fl-pg__csv-error strong", "e => e.map(x => x.textContent)")
    assert errors == ["malformed.csv"]

    # hostile names and headers render as text, never as markup
    page.eval_on_selector(f'.fl-pg__table[data-table="{t_hostile}"]', "d => { d.open = true; }")
    assert HOSTILE_HEADER in page.inner_text(f'.fl-pg__table[data-table="{t_hostile}"]')

    glue = _glue()
    spell = glue.spelling
    text = "\n".join(
        [
            f"ROWS {t_hostile} VALUE ==3",
            f"NULLS {t_hostile}.{spell(HOSTILE_HEADER)} RATE <=50%",
            f"SUM {t_hostile}.{spell('Order ' + chr(34) + 'Total' + chr(34))} VALUE ==60",
            f'NULLS {t_quoted}."Order ID" RATE ==0%',
            f'SUM {t_quoted}."Amount (EUR)" VALUE ==7.75',
        ]
    )
    page.fill("#fl-pg-source", text + "\n")
    # a suggested claim: suggestions are offered for the new tables; add the UNIQUE one
    suggestion = page.locator(f'.fl-pg__sugg[data-claim^="UNIQUE {t_quoted}."]').first
    suggestion.click()
    final = page.input_value("#fl-pg-source")
    assert final.startswith(text) and f"UNIQUE {t_quoted}." in final
    files = {t_hostile: FIXTURES / "hostile.csv", t_quoted: FIXTURES / "quoted_names.csv"}
    want = expected(final, files)
    assert [v for v, _ in want] == ["pass"] * 6
    assert run_check(page, None) == want

    page.wait_for_timeout(500)
    assert seen.dialogs == []
    injected = page.evaluate(
        "() => document.querySelectorAll('img[src=\"x\"], #fl-playground script').length"
    )
    assert injected == 0
    assert seen.page_errors == []
    assert_csv_never_sent(seen, CSV_SECRET.encode(), b"onerror", b"10,1.50")


# -- (5) a slow query and a dead worker: ERROR, then the page works -----------------------------


def test_timeout_is_an_error_then_the_page_recovers(make_context: Context, site_url: str) -> None:
    page, seen = open_playground(make_context, site_url, "?fl-timeout=2&fl-slow=1")
    wait_idle_verdicts(page)
    got = cards(page)
    assert ("error", "FL702 query timed out after 2s") in got
    assert page.locator(".fl-pg__notice").count() >= 1  # "the database was restarted"
    assert run_check(page) == expected(default_example())
    assert worker_counts(page)["blob"] == 2  # the boot, and one restart
    assert seen.page_errors == []


_CRASH = """async () => {
  const s = document.querySelector('script[src*="assets/playground/playground.js"]');
  const base = s.src.replace(/playground\\.js(\\?.*)?$/, "");
  const m = await import(base + "engine.js");  // the same module instance playground.js holds
  // startEngine is idempotent per Pyodide instance: this is the page's own engine
  const engine = await m.startEngine({ pyodide: window.__fl.pyodide });
  if (typeof engine.simulateWorkerError !== "function") return false;
  engine.simulateWorkerError();
  return true;
}"""


def test_worker_error_is_an_error_then_the_page_recovers(
    make_context: Context, site_url: str
) -> None:
    # fl-slow keeps the first query running, so the worker fails with a query in flight
    page, seen = open_playground(make_context, site_url, "?fl-slow=1")
    page.wait_for_function(
        "() => document.querySelector('[data-action=check]').textContent === 'Checking…'",
        timeout=BOOT_MS,
    )
    page.wait_for_timeout(500)
    assert page.evaluate(_CRASH) is True
    wait_idle_verdicts(page)
    got = cards(page)
    assert any(v == "error" and "simulated worker failure" in why for v, why in got), got
    assert page.locator(".fl-pg__notice").count() >= 1
    assert run_check(page) == expected(default_example())
    assert seen.page_errors == []


# -- (6) instant navigation keeps one engine and the visitor's work ------------------------------


def test_instant_navigation_keeps_one_engine_and_the_editor(
    make_context: Context, site_url: str
) -> None:
    page, seen = open_playground(make_context, site_url)
    wait_idle_verdicts(page)
    path = FIXTURES / "quoted_names.csv"
    load_csv(page, [(path.name, path.read_bytes())], ["quoted_names"])
    text = "ROWS quoted_names VALUE ==4\n# my own text\n"
    page.fill("#fl-pg-source", text)
    page.evaluate("window.__marker = 42")
    before = worker_counts(page)
    assert before == {"blob": 1, "pyodide": 1}

    page.click("nav.md-tabs a:has-text('Guide')")
    page.wait_for_url("**/guide/**")
    page.click("nav.md-tabs a:has-text('Playground')")
    page.wait_for_url("**/playground/")
    page.wait_for_selector('.fl-pg__table[data-table="quoted_names"]', timeout=60_000)
    page.wait_for_timeout(1000)

    assert page.evaluate("window.__marker") == 42  # no reload: instant navigation
    assert worker_counts(page) == before  # one Pyodide, one DuckDB worker
    assert page.locator("#fl-playground").count() == 1
    assert page.input_value("#fl-pg-source") == text
    assert table_list(page) == ["customers", "orders", "quoted_names"]
    assert (
        run_check(page) == expected(text, {"quoted_names": path}) == [("pass", cards(page)[0][1])]
    )
    assert worker_counts(page) == before
    assert seen.page_errors == []


# -- WebKit: the editor's transparent textarea sits exactly on its highlight overlay -------------


_ALIGN = """() => {
  const t = document.querySelector('#fl-pg-source'), hl = document.querySelector('.fl-pg__hl');
  const code = hl.querySelector('code') || hl;
  const a = t.getBoundingClientRect(), b = hl.getBoundingClientRect();
  const ts = getComputedStyle(t), hs = getComputedStyle(code);
  return {
    rect: [a.left - b.left, a.top - b.top, a.width - b.width, a.height - b.height].map(Math.abs),
    client: [t.clientWidth, hl.clientWidth], scrollH: [t.scrollHeight, hl.scrollHeight],
    top: [t.scrollTop, hl.scrollTop],
    font: [ts.fontFamily === hs.fontFamily, ts.fontSize === hs.fontSize,
           ts.lineHeight === hs.lineHeight],
    ws: [ts.whiteSpace, hs.whiteSpace]
  };
}"""


def test_webkit_editor_overlay_alignment(
    playwright_api: Any, make_context: Context, site_url: str
) -> None:
    import os

    try:
        webkit = playwright_api.webkit.launch()
    except Exception as exc:
        if os.environ.get("FL_E2E_WEBKIT") == "1":
            raise
        pytest.skip(f"Playwright WebKit is not installed here ({str(exc).splitlines()[0]})")
    try:
        context, _ = make_context(engine=webkit)
        page = context.new_page()
        page.goto(site_url + "playground/")
        page.wait_for_selector("#fl-playground.fl-pg--ready", timeout=BOOT_MS)
        long = (
            "VALUES orders.status IN ['paid', 'open', 'shipped', 'refunded', 'cancelled', 'lost']\n"
        )
        page.fill("#fl-pg-source", page.input_value("#fl-pg-source") + long * 25)
        page.evaluate(
            "() => { const t = document.querySelector('#fl-pg-source'); t.scrollTop = 300;"
            " t.dispatchEvent(new Event('scroll')); }"
        )
        page.wait_for_timeout(300)
        r = page.evaluate(_ALIGN)
        assert max(r["rect"]) <= 1, r
        assert r["client"][0] == r["client"][1], r
        assert abs(r["scrollH"][0] - r["scrollH"][1]) <= 1, r
        assert r["top"][0] > 0 and abs(r["top"][0] - r["top"][1]) <= 1, r
        assert all(r["font"]), r
        assert r["ws"] == ["pre-wrap", "pre-wrap"], r
    finally:
        webkit.close()
