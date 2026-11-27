"""The playground's check path gives the verdicts the real library gives (site spec 5.2, 6).

``glue.fl_check`` runs ``Checker.acheck`` through ``PlaygroundConnector``, whose ``run`` is the
browser's DuckDB-WASM bridge; here ``run`` is Python ``duckdb`` behind the same tagged-JSON
wire format. Every outcome must equal ``factlang`` checking the same database through a
``DBAPIConnector``.
"""

from __future__ import annotations

import ast
import asyncio
import html
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

import factlang as fl
from factlang.connectors.base import normalise_rows, rows_from_cursor
from factlang.judge import json_safe
from factlang.sql import RenderedProbe

duckdb = pytest.importorskip("duckdb")

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
_PYTHON = re.compile(r"^```python\n(.*?)^```", re.S | re.M)


def _catalog(glue: ModuleType, con: Any, bridge: ModuleType) -> str:
    tables = []
    for (name,) in con.execute("SELECT table_name FROM duckdb_tables() ORDER BY 1").fetchall():
        cols = con.execute(f'DESCRIBE "{name}"').fetchall()
        (count,) = con.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()
        info = glue.fl_table_info(name, count, json.dumps([[c[0], c[1]] for c in cols]), "sample")
        tables.append(json.loads(info))
    return glue.fl_catalog(json.dumps(tables))


def _ours(glue: ModuleType, bridge: ModuleType, con: Any, text: str, **kw: Any) -> dict[str, Any]:
    run = bridge.make_run(con)
    out = asyncio.run(glue.fl_check(text, run, _catalog(glue, con, bridge), **kw))
    return json.loads(out)


def _theirs(con: Any, text: str) -> fl.Report:
    return fl.Checker(fl.DBAPIConnector(con, "duckdb")).check(text)


def _examples(glue: ModuleType) -> list[dict[str, str]]:
    return json.loads(glue.SAMPLE_EXAMPLES_JSON)


def _types(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _types(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_types(v) for v in value]
    return type(value).__name__


def test_sample_sql_is_the_getting_started_shop(glue: ModuleType) -> None:
    block = _PYTHON.findall((DOCS / "getting-started.md").read_text(encoding="utf-8"))[0]
    statements = [
        textwrap.dedent(s).strip() for s in re.findall(r'conn\.execute\("""(.*?)"""\)', block, re.S)
    ]
    assert len(statements) == 2
    assert list(glue.SAMPLE_SQL) == statements
    assert json.loads(glue.SAMPLE_SQL_JSON) == statements


def test_examples_declare_a_name_text_and_default_action(glue: ModuleType) -> None:
    examples = _examples(glue)
    assert [e["name"] for e in examples] == [
        "Sample shop — an agent's answer",
        "Sample shop — data contract",
    ]
    for example in examples:
        assert example["action"] == "check"
        assert not fl.lint(example["text"]).has_errors
        assert "shipped_at" not in example["text"], "sample examples are date-free (share links)"


@pytest.mark.parametrize("index", [0, 1])
def test_sample_examples_match_the_library(
    glue: ModuleType, bridge: ModuleType, index: int
) -> None:
    con = bridge.shop(glue)
    text = _examples(glue)[index]["text"]
    report = _theirs(con, text)
    ours = _ours(glue, bridge, con, text)
    assert len(ours["outcomes"]) == len(report.outcomes)
    for mine, ref in zip(ours["outcomes"], report.outcomes, strict=True):
        theirs = ref.to_dict()
        assert mine["claim_text"] == theirs["claim_text"]
        assert mine["verdict"] == theirs["verdict"], mine["claim_text"]
        assert mine["explanation"] == theirs["explanation"], mine["claim_text"]
        assert mine["facts"] == theirs["facts"], mine["claim_text"]
    assert ours["ok"] == report.ok
    assert ours["verified"] == report.verified
    assert ours["counts"] == {v.value: n for v, n in report.summary().items()}


@pytest.mark.parametrize("index", [0, 1])
def test_sample_example_facts_keep_their_python_types(
    glue: ModuleType, bridge: ModuleType, index: int
) -> None:
    con = bridge.shop(glue)
    text = _examples(glue)[index]["text"]
    report = _theirs(con, text)
    run = bridge.make_run(con)
    mine = asyncio.run(glue.acheck_report(text, run, _catalog(glue, con, bridge)))
    for a, b in zip(mine.outcomes, report.outcomes, strict=True):
        assert dict(a.facts) == dict(b.facts)
        assert _types(dict(a.facts)) == _types(dict(b.facts)), a.claim_text


@pytest.mark.parametrize("index", [0, 1])
def test_every_probe_rebuilds_the_rows_python_duckdb_returns(
    glue: ModuleType, bridge: ModuleType, index: int
) -> None:
    con = bridge.shop(glue)
    text = _examples(glue)[index]["text"]
    log: list[dict[str, Any]] = []
    run = bridge.make_run(con, log)
    conn = glue.PlaygroundConnector(run)
    for probe in fl.Checker(dialect="duckdb").compile(text):
        if not isinstance(probe, RenderedProbe):
            continue
        rebuilt = asyncio.run(conn.aexecute(probe, timeout_s=30)).rows
        sql, values = probe.bind("qmark")
        cur = con.cursor()
        cur.execute(sql, list(values))
        native = normalise_rows(probe, rows_from_cursor(cur.description, cur.fetchall()))
        assert rebuilt == native, probe.sql_template
        assert _types(rebuilt) == _types(native), probe.sql_template
    assert log, "the probes ran through the bridge"


def test_agent_answer_example_is_the_landing_ledger(glue: ModuleType, bridge: ModuleType) -> None:
    home = (DOCS / "overrides" / "home.html").read_text(encoding="utf-8")
    rows = re.findall(
        r'<div class="fl-ledger__row" data-verdict="(\w+)">\s*'
        r'<code class="fl-ledger__claim[^"]*">(.*?)</code>.*?'
        r'<span class="fl-ledger__why">(.*?)</span>',
        home,
        re.S,
    )
    con = bridge.shop(glue)
    ours = _ours(glue, bridge, con, _examples(glue)[0]["text"])
    got = [(o["verdict"], o["claim_text"], o["explanation"]) for o in ours["outcomes"]]
    assert got == [(v, html.unescape(c), html.unescape(w)) for v, c, w in rows]
    assert {o["verdict"] for o in ours["outcomes"]} >= {"pass", "fail", "no_data"}


def test_data_contract_example_all_passes(glue: ModuleType, bridge: ModuleType) -> None:
    con = bridge.shop(glue)
    ours = _ours(glue, bridge, con, _examples(glue)[1]["text"])
    assert [o["verdict"] for o in ours["outcomes"]] == ["pass"] * len(ours["outcomes"])
    assert ours["verified"] is True and ours["ok"] is True


def test_fl_check_carries_sql_params_and_timing(glue: ModuleType, bridge: ModuleType) -> None:
    con = bridge.shop(glue)
    text = "COUNT orders VALUE ==1400 WHERE status = 'paid'\nBOGUS orders\n"
    ours = _ours(glue, bridge, con, text)
    assert set(ours) == {"ok", "verified", "counts", "outcomes", "diagnostics"}
    count, bogus = ours["outcomes"]
    assert count["verdict"] == "pass"
    assert count["sql"] == "SELECT COUNT(*) AS row_count FROM orders WHERE status = 'paid'"
    assert count["params"] == [["p1", "paid", "string"]]
    assert isinstance(count["elapsed_ms"], int) and count["elapsed_ms"] >= 0
    assert bogus["verdict"] == "invalid"
    assert bogus["sql"] is None and bogus["params"] == [] and bogus["elapsed_ms"] is None
    assert bogus["diagnostics"]
    assert ours["ok"] is False


def test_typed_literals_filter_like_the_library(glue: ModuleType, bridge: ModuleType) -> None:
    """Every parameter family bound through ``CAST(? AS …)`` selects the rows DBAPI does."""
    con = duckdb.connect()
    # DBAPI binds an aware timestamp as TIMESTAMPTZ, compared in the session time zone; the
    # playground binds naive UTC (the duckdb profile's convention). They agree under UTC.
    con.execute("SET GLOBAL TimeZone = 'UTC'")
    con.execute(
        "CREATE TABLE events AS SELECT i AS id, CAST(i AS DECIMAL(10, 2)) / 4 AS amount, "
        "i * 0.5 AS score, 'k' || (i % 3) AS kind, i % 2 = 0 AS even, "
        "DATE '2024-01-01' + CAST(i AS INTEGER) AS day, "
        "TIMESTAMP '2024-01-01 00:00:00' + to_hours(i) AS at FROM range(1, 101) AS t(i)"
    )
    text = "\n".join(
        [
            "COUNT events VALUE ==50 WHERE id > 50",
            "COUNT events VALUE ==90 WHERE amount > 2.5",
            "COUNT events VALUE ==50 WHERE score <= 25.0",
            "COUNT events VALUE ==33 WHERE kind = 'k0'",
            "COUNT events VALUE ==50 WHERE even = TRUE",
            "COUNT events VALUE ==40 WHERE day > DATE '2024-03-01'",
            "COUNT events VALUE ==51 WHERE at >= TIMESTAMP '2024-01-03T02:00:00Z'",
            "SUM events.amount VALUE ==1262.5",
            "AVG events.score VALUE ==25.25",
            "RANGE events.day BETWEEN [DATE '2024-01-02', DATE '2024-04-10']",
        ]
    )
    report = _theirs(con, text)
    ours = _ours(glue, bridge, con, text)
    for mine, ref in zip(ours["outcomes"], report.outcomes, strict=True):
        theirs = ref.to_dict()
        assert (mine["verdict"], mine["explanation"], mine["facts"]) == (
            theirs["verdict"],
            theirs["explanation"],
            theirs["facts"],
        ), mine["claim_text"]
    assert all(o["verdict"] == "pass" for o in ours["outcomes"]), ours["outcomes"]


def test_type_claims_use_the_static_catalog_families(glue: ModuleType, bridge: ModuleType) -> None:
    con = bridge.shop(glue)
    text = (
        "TYPE orders.id IS integer\nTYPE orders.amount IS decimal\nTYPE customers.email IS string\n"
    )
    ours = _ours(glue, bridge, con, text)
    assert [o["verdict"] for o in ours["outcomes"]] == ["pass", "pass", "pass"]
    assert [o.verdict.value for o in _theirs(con, text).outcomes] == ["pass", "pass", "pass"]
    catalog = json.loads(_catalog(glue, con, bridge))
    assert catalog["orders"]["id"] == "integer"
    assert catalog["orders"]["amount"] == "decimal"


def test_a_slow_query_times_out_as_fl702(glue: ModuleType) -> None:
    async def slow(sql: str, params_json: str) -> str:
        await asyncio.sleep(5)
        return json.dumps({"columns": [], "rows": []})

    catalog = json.dumps({"orders": {"id": "integer"}})
    started = asyncio.run(_timed(glue.fl_check("UNIQUE orders.id", slow, catalog, timeout_s=0.2)))
    out, seconds = started
    (outcome,) = json.loads(out)["outcomes"]
    assert outcome["verdict"] == "error"
    assert [d["code"] for d in outcome["diagnostics"]] == ["FL702"]
    assert seconds < 2, "the connector enforces the timeout, not the engine backstop"


async def _timed(coro: Any) -> tuple[Any, float]:
    loop = asyncio.get_running_loop()
    start = loop.time()
    out = await coro
    return out, loop.time() - start


class _BrowserError(Exception):
    """Stands in for Pyodide's JsException: the JS error name leads the message."""


def test_a_browser_timeout_rejection_is_fl702(glue: ModuleType) -> None:
    async def js_timeout(sql: str, params_json: str) -> str:
        raise _BrowserError("TimeoutError: query timed out after 30s")

    out = asyncio.run(
        glue.fl_check(
            "UNIQUE orders.id", js_timeout, json.dumps({"orders": {"id": "integer"}}), timeout_s=30
        )
    )
    (outcome,) = json.loads(out)["outcomes"]
    assert [d["code"] for d in outcome["diagnostics"]] == ["FL702"]


def test_a_failing_query_is_fl700(glue: ModuleType) -> None:
    async def broken(sql: str, params_json: str) -> str:
        raise _BrowserError("Error: Catalog Error: Table with name orders does not exist!")

    out = asyncio.run(
        glue.fl_check(
            "UNIQUE orders.id", broken, json.dumps({"orders": {"id": "integer"}}), timeout_s=30
        )
    )
    (outcome,) = json.loads(out)["outcomes"]
    assert outcome["verdict"] == "error"
    assert [d["code"] for d in outcome["diagnostics"]] == ["FL700"]
    assert "does not exist" in outcome["explanation"]


def test_lint_with_a_catalog_checks_names(glue: ModuleType) -> None:
    catalog = json.dumps({"orders": {"id": "integer", "status": "string"}})
    known = json.loads(glue.fl_lint_with("UNIQUE orders.id\n", catalog))
    assert known["diagnostics"] == [] and known["claims"] == 1
    unknown = json.loads(
        glue.fl_lint_with("UNIQUE orders.nope\nNULLS nothing.x RATE <=5%\n", catalog)
    )
    assert len(unknown["diagnostics"]) == 2
    assert unknown["rendered"]
    without = json.loads(glue.fl_lint("UNIQUE orders.nope\n"))
    assert without["diagnostics"] == []


def test_results_are_json_safe(glue: ModuleType, bridge: ModuleType) -> None:
    con = bridge.shop(glue)
    ours = _ours(glue, bridge, con, _examples(glue)[0]["text"])
    assert json_safe(ours) == ours


def test_glue_imports_neither_js_nor_duckdb_at_module_level() -> None:
    path = ROOT / "site-playground" / "glue.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    top: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            top |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            top.add(node.module.split(".")[0])
    assert not top & {"js", "pyodide", "duckdb"}, top
    code = (
        "import sys; sys.modules['duckdb'] = None; sys.modules['js'] = None; "
        f"ns = {{'__name__': 'glue'}}; exec(open({str(path)!r}).read(), ns); print(ns['smoke']())"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert out.stdout.startswith("ok ")


def test_every_standard_verb_matches_the_library_on_the_integration_shop(
    glue: ModuleType, bridge: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The integration matrix (every verb: pass, fail, no-data, empty) through the bridge."""
    monkeypatch.syspath_prepend(str(ROOT / "tests" / "integration"))
    from expectations import CASES, Approx
    from shop import make_shop

    con = duckdb.connect()
    con.execute("SET GLOBAL TimeZone = 'UTC'")
    make_shop(con)
    catalog = _catalog(glue, con, bridge)
    reference = fl.Checker(fl.DBAPIConnector(con, "duckdb"))
    checked = 0
    for case in CASES:
        if case.catalog != "warehouse":
            continue  # the playground always has a catalog: the loaded tables
        (theirs,) = reference.check(case.claim).outcomes
        out = json.loads(asyncio.run(glue.fl_check(case.claim, bridge.make_run(con), catalog)))
        (mine,) = out["outcomes"]
        assert mine["verdict"] == theirs.verdict.value == case.verdict.value, case.claim
        if any(isinstance(v, Approx) for v in case.facts.values()):
            continue  # wall-clock facts (ages) move between the two runs
        ref = theirs.to_dict()
        assert (mine["explanation"], mine["facts"]) == (ref["explanation"], ref["facts"]), (
            case.claim
        )
        checked += 1
    assert checked >= 40


def test_the_timeout_names_its_limit(glue: ModuleType) -> None:
    async def slow(sql: str, params_json: str) -> str:
        await asyncio.sleep(5)
        return json.dumps({"columns": [], "rows": []})

    catalog = json.dumps({"orders": {"id": "integer"}})
    out = asyncio.run(glue.fl_check("UNIQUE orders.id", slow, catalog, timeout_s=0.2))
    (outcome,) = json.loads(out)["outcomes"]
    assert outcome["explanation"] == "FL702 query timed out after 0.2s"


def test_a_query_waiting_behind_a_restart_is_not_timed_out(glue: ModuleType) -> None:
    """In the browser engine.js owns the timeout (its clock starts when the query reaches the
    worker); Python's own limit is a backstop ``timeout_s + margin_s``."""

    async def queued(sql: str, params_json: str) -> str:
        await asyncio.sleep(0.4)  # waiting in engine.js's queue while the worker restarts
        cell = glue.encode_cell(1, "integer")
        return json.dumps({"columns": [["row_count", "BIGINT"]], "rows": [[cell]]})

    catalog = json.dumps({"orders": {"id": "integer"}})
    claim = "ROWS orders VALUE ==1"
    late = asyncio.run(glue.fl_check(claim, queued, catalog, timeout_s=0.2))
    assert [d["code"] for d in json.loads(late)["outcomes"][0]["diagnostics"]] == ["FL702"]
    ok = asyncio.run(glue.fl_check(claim, queued, catalog, timeout_s=0.2, margin_s=2))
    assert [o["verdict"] for o in json.loads(ok)["outcomes"]] == ["pass"]


def test_a_browser_timeout_reports_the_browser_limit_not_the_backstop(glue: ModuleType) -> None:
    async def js_timeout(sql: str, params_json: str) -> str:
        raise _BrowserError("TimeoutError: query timed out after 30s")

    catalog = json.dumps({"orders": {"id": "integer"}})
    out = asyncio.run(
        glue.fl_check("UNIQUE orders.id", js_timeout, catalog, timeout_s=30, margin_s=120)
    )
    (outcome,) = json.loads(out)["outcomes"]
    assert outcome["explanation"] == "FL702 query timed out after 30s"


def test_infinite_dates_match_the_library(glue: ModuleType, bridge: ModuleType) -> None:
    con = duckdb.connect()
    con.execute("SET GLOBAL TimeZone = 'UTC'")
    con.execute(
        "CREATE TABLE inf AS SELECT * FROM (VALUES (DATE '2024-01-01', TIMESTAMP '2024-01-01'),"
        " ('infinity'::DATE, 'infinity'::TIMESTAMP), ('-infinity'::DATE, '-infinity'::TIMESTAMP))"
        " AS t(d, ts)"
    )
    text = "\n".join(
        [
            "MAX inf.d VALUE >= DATE '2024-01-01'",
            "MIN inf.d VALUE <= DATE '2024-01-01'",
            "MAX inf.ts VALUE >= TIMESTAMP '2024-01-01T00:00:00Z'",
            "RANGE inf.d BETWEEN [DATE '2000-01-01', DATE '2030-01-01']",
        ]
    )
    report = _theirs(con, text)
    ours = _ours(glue, bridge, con, text)
    for mine, ref in zip(ours["outcomes"], report.outcomes, strict=True):
        theirs = ref.to_dict()
        assert (mine["verdict"], mine["explanation"], mine["facts"]) == (
            theirs["verdict"],
            theirs["explanation"],
            theirs["facts"],
        ), mine["claim_text"]
