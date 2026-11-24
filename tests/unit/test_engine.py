"""Checker orchestration without a real warehouse (spec 7, 9.3, 12 FL7xx)."""

from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any

import pytest

from factlang.connectors import CallableConnector, ExecutionError, QueryResult
from factlang.engine import Checker, CostEstimate, Measurement, Report, string_quoter
from factlang.ir import ColumnAggregate, Metric
from factlang.judge import Outcome, Verdict
from factlang.semantics.catalog import NullCatalog, StaticCatalog
from factlang.semantics.refs import Ident, TableRef
from factlang.sql import RenderedProbe

ORDERS = TableRef(None, None, Ident("orders"))


def rows_fn(rows: list[dict[str, Any]]) -> Any:
    """A host function answering every probe with ``rows``; a fused probe's ``m<i>__`` aliases
    are answered from the unprefixed names."""
    calls: list[tuple[str, object]] = []

    def fn(sql: str, params: object) -> list[dict[str, Any]]:
        calls.append((sql, params))
        aliases = re.findall(r"\bAS (m\d+__\w+)", sql)
        if not aliases:
            return rows
        return [{a: r[a.split("__", 1)[1]] for a in aliases} for r in rows]

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


def test_invalid_unprobeable_and_order() -> None:
    text = (
        "ROWS orders VALUE >= 1\n"
        "ROWS orders VALUE >= x\n"
        "FOO bar\n"
        "NOTE orders.total IS eur BECAUSE 'finance'\n"
        "COUNT orders VALUE == 1 WHERE (\n"
        "ROWS orders VALUE == 7"
    )
    fn = rows_fn([{"row_count": 7}])
    report = Checker(CallableConnector(fn, "duckdb"), catalog=NullCatalog()).check(text)
    verdicts = [o.verdict for o in report.outcomes]
    assert verdicts == [
        Verdict.PASS,
        Verdict.INVALID,
        Verdict.INVALID,
        Verdict.UNPROBEABLE,
        Verdict.INVALID,
        Verdict.PASS,
    ]
    invalid = report.outcomes[1]
    assert invalid.claim_text == "ROWS orders VALUE >= x"
    assert invalid.explanation.startswith("FL305")
    assert [d["code"] for d in invalid.diagnostics if d["severity"] == "error"] == ["FL305"]
    assert report.outcomes[2].explanation.startswith("FL300") and report.outcomes[2].verb == "FOO"
    assert report.outcomes[4].claim_text.startswith("COUNT orders")
    assert report.outcomes[3].explanation == "semantic fact; recorded, not measured"
    assert report.outcomes[3].semantic_hash.startswith("sha256:")
    assert not report.ok
    assert {"FL305", "FL300"} <= set(report.diagnostics.codes())
    # the two ROWS claims are different claims over the same table: fused into one read
    assert len(fn.calls) == 1


def test_info_diagnostics_reach_the_outcome() -> None:
    fn = rows_fn([{"row_count": 7}])
    (o,) = (
        Checker(CallableConnector(fn, "duckdb"), catalog=NullCatalog())
        .check("ROWS orders VALUE >= 1")
        .outcomes
    )
    assert [d["code"] for d in o.diagnostics] == ["FL313"]
    assert o.verdict is Verdict.PASS


def test_use_directive_applies_and_bad_directive_is_reported() -> None:
    fn = rows_fn([{"row_count": 7}])
    text = (
        "USE shop\nROWS orders VALUE >= 1\n"
        "USE a.b.c\nROWS orders VALUE == 7\nNOTE orders.a IS x BECAUSE 'y'\n"
        "USE shop\nROWS orders VALUE == 7"
    )
    report = Checker(CallableConnector(fn, "duckdb"), catalog=NullCatalog()).check(text)
    # the bad USE poisons the namespace until the next valid USE
    assert [o.verdict for o in report.outcomes] == [
        Verdict.PASS,
        Verdict.INVALID,
        Verdict.INVALID,
        Verdict.PASS,
    ]
    for o in report.outcomes[1:3]:
        assert o.explanation.startswith("FL302")
        assert [d["code"] for d in o.diagnostics] == ["FL302"]
    assert report.outcomes[1].claim_text == "ROWS orders VALUE == 7"
    assert "shop" in report.outcomes[0].canonical_text
    assert "shop" in report.outcomes[3].canonical_text
    assert "FL302" in report.diagnostics.codes()
    assert not report.ok
    assert '"shop"' in fn.calls[0][0] or "shop." in fn.calls[0][0]


def test_unparseable_use_poisons_and_fails_ok() -> None:
    fn = rows_fn([{"row_count": 7}])
    checker = Checker(CallableConnector(fn, "duckdb"), catalog=NullCatalog())
    report = checker.check("USE 5\nROWS orders VALUE == 7")
    assert [o.verdict for o in report.outcomes] == [Verdict.INVALID]
    assert report.outcomes[0].explanation.startswith("FL203")
    assert not report.ok
    # an error diagnostic alone (no outcome of its own) makes the report not ok
    alone = checker.check("USE a.b.c")
    assert alone.outcomes == () and not alone.ok


def test_invalid_multiline_claim_text_starts_at_its_first_line() -> None:
    text = "COUNT orders \\\n  VALUE == 1 \\\n  WHERE (\nROWS orders VALUE == 7"
    fn = rows_fn([{"row_count": 7}])
    report = Checker(CallableConnector(fn, "duckdb"), catalog=NullCatalog()).check(text)
    assert [o.verdict for o in report.outcomes] == [Verdict.INVALID, Verdict.PASS]
    bad = report.outcomes[0]
    assert bad.verb == "COUNT"
    assert bad.claim_text == "COUNT orders \\\n  VALUE == 1 \\\n  WHERE ("
    assert bad.explanation.startswith("FL206")


def test_catalog_verbs_use_the_given_catalog() -> None:
    cat = StaticCatalog({"orders": {"id": "integer"}})
    report = Checker(dialect="duckdb", catalog=cat).check(
        "EXISTS orders\nEXISTS orders COLUMN id\n"
        "TYPE orders.id IS integer\nTYPE orders.id IS string"
    )
    assert [o.verdict for o in report.outcomes] == [
        Verdict.PASS,
        Verdict.PASS,
        Verdict.PASS,
        Verdict.FAIL,
    ]
    assert report.outcomes[0].evidence is not None
    assert report.outcomes[0].evidence.tier == "verified"


def test_catalog_unknown_is_no_data() -> None:
    (o,) = Checker(dialect="duckdb").check("EXISTS orders").outcomes
    assert o.verdict is Verdict.NO_DATA
    assert o.explanation.startswith("catalog could not answer")


class RaisingCatalog:
    def table_exists(self, ref: TableRef) -> bool | None:
        raise ExecutionError("catalog query timed out", code="FL702")

    def columns(self, ref: TableRef) -> dict[str, str] | None:
        raise ExecutionError("catalog query timed out", code="FL702")


def test_catalog_raising_is_error_with_its_code() -> None:
    report = Checker(dialect="duckdb", catalog=RaisingCatalog()).check(
        "EXISTS orders\nTYPE orders.id IS integer"
    )
    for o in report.outcomes:
        assert o.verdict is Verdict.ERROR
        assert "FL702" in o.explanation
        assert [d["code"] for d in o.diagnostics if d["severity"] == "error"] == ["FL702"]
    # the analyser treated the failing catalog as unknown
    assert "FL313" in report.diagnostics.codes()


def test_catalog_raising_other_errors_is_error() -> None:
    class Broken:
        def table_exists(self, ref: TableRef) -> bool | None:
            raise RuntimeError("boom")

        def columns(self, ref: TableRef) -> dict[str, str] | None:
            raise RuntimeError("boom")

    (o,) = Checker(dialect="duckdb", catalog=Broken()).check("EXISTS orders").outcomes
    assert o.verdict is Verdict.ERROR and "boom" in o.explanation


def test_default_catalog_is_warehouse_backed() -> None:
    fn = rows_fn([])
    checker = Checker(CallableConnector(fn, "duckdb"))
    report = checker.check("EXISTS orders\nTYPE orders.id IS integer")
    # an empty information_schema answer: the table does not exist. For EXISTS that is the
    # answer (FAIL); for TYPE it is a name error (FL311, INVALID)
    assert [o.verdict for o in report.outcomes] == [Verdict.FAIL, Verdict.INVALID]
    assert report.outcomes[0].facts == {"exists": False}
    assert "FL311" in report.outcomes[1].explanation
    assert "information_schema" in fn.calls[0][0].lower()


def test_async_only_connector_gets_a_null_catalog() -> None:
    class AsyncOnly:
        dialect = "duckdb"
        paramstyle = "qmark"

        async def aexecute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
            return QueryResult([{"row_count": 3}])

    checker = Checker(AsyncOnly())
    assert isinstance(checker.catalog, NullCatalog)
    report = asyncio.run(checker.acheck("ROWS orders VALUE == 3\nEXISTS orders"))
    assert [o.verdict for o in report.outcomes] == [Verdict.PASS, Verdict.NO_DATA]


def test_compile_returns_probes_and_outcomes() -> None:
    out = Checker(dialect="snowflake").compile(
        "UNIQUE orders.id\nNOTE orders.id IS x BECAUSE 'y'\nBAD\nEXISTS orders"
    )
    assert isinstance(out[0], RenderedProbe) and out[0].dialect == "snowflake"
    assert isinstance(out[1], Outcome) and out[1].verdict is Verdict.UNPROBEABLE
    assert isinstance(out[2], Outcome) and out[2].verdict is Verdict.INVALID
    assert isinstance(out[3], RenderedProbe) and "INFORMATION_SCHEMA" in out[3].sql_template.upper()


def test_compile_render_error_is_error_outcome() -> None:
    (o,) = Checker(dialect="no_such_dialect").compile("ROWS orders VALUE >= 1")
    assert isinstance(o, Outcome) and o.verdict is Verdict.ERROR
    assert o.explanation.startswith("FL504")


def test_check_render_error_is_error_outcome() -> None:
    fn = rows_fn([])
    (o,) = (
        Checker(CallableConnector(fn, "duckdb"), dialect="no_such_dialect", catalog=NullCatalog())
        .check("ROWS orders VALUE >= 1")
        .outcomes
    )
    assert o.verdict is Verdict.ERROR and o.explanation.startswith("FL504")
    assert fn.calls == []


def test_needs_a_dialect_or_connector() -> None:
    with pytest.raises(ValueError, match="dialect"):
        Checker()
    with pytest.raises(ValueError, match="connector"):
        Checker(dialect="duckdb").check("ROWS orders VALUE >= 1")
    with pytest.raises(ValueError, match="connector"):
        Checker(dialect="duckdb").measure([ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,))])
    with pytest.raises(ValueError, match="concurrency"):
        Checker(dialect="duckdb", concurrency=0)
    # no probe to execute: no connector needed (render failures are ERROR outcomes)
    (o,) = Checker(dialect="no_such_dialect").check("ROWS orders VALUE >= 1").outcomes
    assert o.verdict is Verdict.ERROR and o.explanation.startswith("FL504")
    (o,) = asyncio.run(Checker(dialect="no_such_dialect").acheck("ROWS t VALUE >= 1")).outcomes
    assert o.verdict is Verdict.ERROR


class Hanging:
    """A connector that ignores ``timeout_s``."""

    dialect = "duckdb"
    paramstyle = "qmark"

    def __init__(self, delay: float) -> None:
        self.delay = delay

    def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
        time.sleep(self.delay)
        return QueryResult([{"row_count": 1}])

    async def aexecute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
        await asyncio.sleep(self.delay)
        return QueryResult([{"row_count": 1}])


def test_engine_timeout_backstop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Checker, "TIMEOUT_GRACE_S", 0.05)
    slow = Checker(Hanging(2.0), catalog=NullCatalog(), timeout_s=0.05)
    start = time.monotonic()
    (o,) = slow.check("ROWS orders VALUE >= 1").outcomes
    assert time.monotonic() - start < 1.5
    assert o.verdict is Verdict.ERROR and o.explanation.startswith("FL702")
    (a,) = asyncio.run(slow.acheck("ROWS orders VALUE >= 1")).outcomes
    assert a.verdict is Verdict.ERROR and a.explanation.startswith("FL702")
    (m,) = slow.measure([ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,))])
    assert m.error is not None and m.error.code == "FL702" and m.verdict_hint is Verdict.ERROR
    # within the budget the answer is used
    fast = Checker(Hanging(0.0), catalog=NullCatalog(), timeout_s=0.05, concurrency=1)
    (ok,) = fast.check("ROWS orders VALUE >= 1").outcomes
    assert ok.verdict is Verdict.PASS
    none = Checker(Hanging(0.0), catalog=NullCatalog(), timeout_s=None)
    assert none.check("ROWS orders VALUE >= 1").ok
    assert asyncio.run(none.acheck("ROWS orders VALUE >= 1")).ok


def test_public_result_field_order() -> None:
    import dataclasses

    assert [f.name for f in dataclasses.fields(Measurement)] == [
        "plan",
        "payload",
        "verdict_hint",
        "evidence",
        "error",
    ]
    assert [f.name for f in dataclasses.fields(CostEstimate)] == [
        "semantic_hash_or_plan_key",
        "sql",
        "bytes",
        "method",
    ]
    est = CostEstimate("k", None, None, "unknown")
    assert est.key == "k"
    with pytest.raises(AttributeError):
        est.key = "x"  # type: ignore[misc]


def test_shape_error_is_error() -> None:
    fn = rows_fn([{"row_count": 1}, {"row_count": 2}])
    (o,) = (
        Checker(CallableConnector(fn, "duckdb"), catalog=NullCatalog())
        .check("ROWS orders VALUE >= 1")
        .outcomes
    )
    assert o.verdict is Verdict.ERROR and "expected one row" in o.explanation
    (m,) = Checker(CallableConnector(fn, "duckdb")).measure(
        [ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,))]
    )
    assert m.error is not None and m.error.code == "FL700" and m.payload is None


def test_unexpected_connector_exception_is_fl700() -> None:
    class Weird:
        dialect = "duckdb"
        paramstyle = "qmark"

        def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
            raise RuntimeError("socket closed")

        def dry_run_bytes(self, probe: RenderedProbe) -> int:
            raise RuntimeError("no dry run today")

    (o,) = Checker(Weird(), catalog=NullCatalog()).check("ROWS orders VALUE >= 1").outcomes
    assert o.verdict is Verdict.ERROR and o.explanation.startswith("FL700")
    (o2,) = (
        Checker(Weird(), catalog=NullCatalog(), max_bytes=10)
        .check("ROWS orders VALUE >= 1")
        .outcomes
    )
    assert o2.verdict is Verdict.ERROR and "no dry run today" in o2.explanation
    (est,) = Checker(Weird()).estimate("ROWS orders VALUE >= 1")
    assert est.bytes is None and est.method == "unknown"


def test_estimate_skips_unprobeable_and_reports_render_errors() -> None:
    out = Checker(dialect="duckdb").estimate(
        "NOTE orders.id IS x BECAUSE 'y'\nBAD\nROWS t VALUE >= 1"
    )
    assert len(out) == 1 and isinstance(out[0], CostEstimate)
    assert out[0].method == "unknown" and out[0].semantic_hash_or_plan_key == out[0].key
    (bad,) = Checker(dialect="no_such_dialect").estimate("ROWS t VALUE >= 1")
    assert bad.sql is None and bad.bytes is None and bad.method == "unknown"


def test_report_serialisation() -> None:
    fn = rows_fn([{"row_count": 7}])
    report = Checker(CallableConnector(fn, "duckdb"), catalog=NullCatalog()).check(
        "ROWS orders VALUE >= 1\nROWS orders VALUE >= 100\nNOTE orders.a IS x BECAUSE 'a | b'"
    )
    assert isinstance(report, Report)
    summary = report.summary()
    assert summary[Verdict.PASS] == 1 and summary[Verdict.FAIL] == 1
    assert summary[Verdict.UNPROBEABLE] == 1 and summary[Verdict.ERROR] == 0
    assert set(summary) == set(Verdict)
    d = report.to_dict()
    assert d["ok"] is False and d["summary"]["fail"] == 1  # type: ignore[index]
    assert json.loads(report.to_json()) == json.loads(json.dumps(d))
    assert report.to_json(indent=None).count("\n") == 0
    md = report.to_markdown()
    assert "| fail |" in md and "ROWS orders VALUE >= 100" in md
    assert "a \\| b" in md


def test_report_ok_only_without_fail_error_invalid() -> None:
    fn = rows_fn([{"row_count": 7}])
    c = Checker(CallableConnector(fn, "duckdb"), catalog=NullCatalog())
    assert c.check("ROWS orders VALUE >= 1\nNOTE orders.a IS x BECAUSE 'y'").ok
    assert not c.check("BAD").ok
    assert c.check("").ok and c.check("").outcomes == ()


def test_evidence_inlines_with_the_dialect_string_quoter() -> None:
    fn = rows_fn([{"row_count": 0}])
    (o,) = (
        Checker(CallableConnector(fn, "bigquery"), catalog=NullCatalog())
        .check("COUNT orders VALUE == 0 WHERE status = 'it''s'")
        .outcomes
    )
    assert o.evidence is not None and "'it\\'s'" in (o.evidence.sql or "")
    assert string_quoter("duckdb")("it's") == "'it''s'"
    assert string_quoter("mysql")("a\\b'") == "'a\\\\b\\''"


def test_acheck_uses_native_aexecute() -> None:
    class Async:
        dialect = "duckdb"
        paramstyle = "qmark"

        def __init__(self) -> None:
            self.calls = 0

        def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
            raise AssertionError("sync path used")

        async def aexecute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
            self.calls += 1
            return QueryResult([{"row_count": 3}], bytes_processed=12)

    conn = Async()
    report = asyncio.run(
        Checker(conn, catalog=NullCatalog()).acheck("ROWS orders VALUE == 3\nEXISTS orders")
    )
    assert [o.verdict for o in report.outcomes] == [Verdict.PASS, Verdict.NO_DATA]
    assert conn.calls == 1
    ev = report.outcomes[0].evidence
    assert ev is not None and ev.bytes_processed == 12 and ev.duration_ms is not None
    out = asyncio.run(Checker(conn).ameasure([ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,))]))
    assert isinstance(out[0], Measurement) and out[0].payload == {"row_count": 3}


def test_acheck_cost_guard_and_errors() -> None:
    class Guarded:
        dialect = "duckdb"
        paramstyle = "qmark"

        def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
            raise ExecutionError("FL700 nope")

        def dry_run_bytes(self, probe: RenderedProbe) -> int:
            return 5000

    out = asyncio.run(
        Checker(Guarded(), catalog=NullCatalog(), max_bytes=10).acheck("ROWS orders VALUE >= 1")
    )
    assert out.outcomes[0].explanation.startswith("FL701")
    out2 = asyncio.run(Checker(Guarded(), catalog=NullCatalog()).acheck("ROWS orders VALUE >= 1"))
    assert out2.outcomes[0].explanation.startswith("FL700")


def test_estimate_never_consults_a_catalog() -> None:
    asked: list[TableRef] = []

    class Untouchable:
        def table_exists(self, ref: TableRef) -> bool | None:
            asked.append(ref)
            return True

        def columns(self, ref: TableRef) -> dict[str, str] | None:
            asked.append(ref)
            return {"id": "integer"}

    (est,) = Checker(dialect="duckdb", catalog=Untouchable()).estimate("ROWS orders VALUE >= 1")
    assert est.sql is not None and est.method == "unknown"
    assert asked == []  # the analyser swallows catalog errors, so record instead of raising
