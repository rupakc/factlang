"""The verb matrix on every ``verified`` engine (spec 8.5, 13, 14.2, 18 R7, 19 B4; Task 20).

Parametrised over ``duckdb`` and the service-container engines of ``engines.py`` (postgres,
mysql, clickhouse, tsql, trino). The shop fixture and the expectations are the DuckDB ones
(``conftest.make_shop``, ``expectations.py``): a verified engine must reach the same verdicts
and the same facts. An engine that is not reachable is skipped, unless
``FACTLANG_REQUIRE_ENGINES`` names it (CI), when the test fails instead.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

import pytest
from conftest import BIG, make_shop
from engines import ENGINE_NAMES, Style, open_engine
from expectations import CASES, COMPOSITE_JOIN, COVERAGE_FAIL, ENGINE_CASES, Case

from factlang.connectors import DBAPIConnector, QueryResult, run_metadata
from factlang.engine import Checker
from factlang.ir import (
    KeyOverlap,
    SampleSpec,
    ValueMembership,
)
from factlang.judge import Outcome, Verdict
from factlang.semantics.catalog import NullCatalog
from factlang.semantics.refs import ColumnRef, Ident, TableRef
from factlang.sql import RenderedProbe

pytestmark = pytest.mark.integration


@dataclass
class Shop:
    name: str
    connection: Any
    style: Style
    connector: DBAPIConnector


class Logged:
    """Records every probe a connector executes."""

    def __init__(self, inner: DBAPIConnector) -> None:
        self.inner = inner
        self.dialect = inner.dialect
        self.paramstyle = inner.paramstyle
        self.executed: list[RenderedProbe] = []

    def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
        self.executed.append(probe)
        return self.inner.execute(probe, timeout_s=timeout_s)


@pytest.fixture(scope="module", params=ENGINE_NAMES)
def shop(request: pytest.FixtureRequest) -> Iterator[Shop]:
    connection, style = open_engine(request.param)
    make_shop(connection, style=style)
    yield Shop(request.param, connection, style, DBAPIConnector(connection, request.param))
    close = getattr(connection, "close", None)
    if close is not None:
        close()


def _t(name: str) -> TableRef:
    return TableRef(None, None, Ident(name))


def _assert_case(outcome: Outcome, case: Case, engine: str) -> None:
    where = (engine, case.claim, outcome.explanation, dict(outcome.facts))
    assert outcome.verdict is case.verdict, where
    assert outcome.verb == case.verb
    for name, want in case.facts.items():
        assert name in outcome.facts, (*where, name)
        got = outcome.facts[name]
        assert want == got, (engine, case.claim, name, got, want)


ALL_CASES = (*CASES, *COMPOSITE_JOIN, COVERAGE_FAIL, *ENGINE_CASES)


@pytest.mark.parametrize("case", ALL_CASES, ids=[f"{c.id}:{c.claim}" for c in ALL_CASES])
def test_every_standard_verb(shop: Shop, case: Case) -> None:
    checker = Checker(shop.connector, catalog=NullCatalog() if case.catalog == "null" else None)
    (outcome,) = checker.check(case.claim).outcomes
    _assert_case(outcome, case, shop.name)
    if outcome.verdict in (Verdict.PASS, Verdict.FAIL, Verdict.NO_DATA):
        assert outcome.evidence is not None
        assert outcome.evidence.dialect == shop.name
        assert outcome.evidence.tier == "verified"
        if case.verb not in ("EXISTS", "TYPE"):
            assert outcome.evidence.sql and outcome.evidence.method == "exact"


def _comparable(o: Outcome) -> tuple[object, ...]:
    facts = dict(o.facts)
    if o.verb == "FRESH":  # wall clock: two runs see different server_now values
        facts.pop("age_seconds", None)
        return (o.claim_text, o.verdict, tuple(sorted(facts.items())))
    return (o.claim_text, o.verdict, o.explanation, tuple(sorted(facts.items(), key=str)))


def test_batch_fused_and_unfused_agree_with_expectations(shop: Shop) -> None:
    cases = [c for c in ALL_CASES if c.catalog == "warehouse"]
    text = "\n".join(c.claim for c in cases)
    fused_conn, plain_conn = Logged(shop.connector), Logged(shop.connector)
    fused = Checker(fused_conn).check(text)
    plain = Checker(plain_conn, fuse=False).check(text)
    for outcome, case in zip(fused.outcomes, cases, strict=True):
        _assert_case(outcome, case, shop.name)
    assert [_comparable(o) for o in fused.outcomes] == [_comparable(o) for o in plain.outcomes]
    assert len(fused_conn.executed) < len(plain_conn.executed)


def test_hostile_literal_is_bound(shop: Shop) -> None:
    hostile = "x''; DROP TABLE orders; --"
    logged = Logged(shop.connector)
    (outcome,) = (
        Checker(logged, catalog=NullCatalog())
        .check(f"COUNT orders VALUE == 0 WHERE status = '{hostile}'")
        .outcomes
    )
    assert outcome.verdict is Verdict.PASS, outcome.explanation
    assert outcome.facts["value"] == 0
    probe = logged.executed[-1]
    assert "DROP" not in probe.sql_template
    assert [p.value for p in probe.params] == ["x'; DROP TABLE orders; --"]
    (rows,) = (
        Checker(shop.connector, catalog=NullCatalog()).check("ROWS orders VALUE == 100").outcomes
    )
    assert rows.verdict is Verdict.PASS, rows.explanation


def test_quoted_table_and_reserved_column(shop: Shop) -> None:
    text = (
        'ROWS "Order Items" VALUE == 3\n'
        'UNIQUE "Order Items".order\n'
        'SUM "Order Items".order VALUE == 6\n'
        'EXISTS "Order Items" COLUMN order\n'
        'TYPE "Order Items".order IS integer'
    )
    report = Checker(shop.connector).check(text)
    assert [o.verdict for o in report.outcomes] == [Verdict.PASS] * 5, [
        o.explanation for o in report.outcomes
    ]


# -- integer overflow, T-SQL spellings ---------------------------------------------------------


def test_integer_sum_past_32_bits_is_exact_or_a_loud_error(shop: Shop) -> None:
    (outcome,) = Checker(shop.connector).check(f"SUM big_t.v VALUE == {2 * BIG}").outcomes
    if shop.name == "tsql":
        # SUM stays native on T-SQL: an INT sum past 2^31 is an arithmetic overflow
        assert outcome.verdict is Verdict.ERROR, (outcome.verdict, dict(outcome.facts))
        assert outcome.explanation.startswith("FL700"), outcome.explanation
    else:
        assert outcome.verdict is Verdict.PASS, outcome.explanation
        assert outcome.facts["value"] == 2 * BIG
    # never a wrong number: a FAIL here would mean a silently wrapped sum
    assert outcome.verdict is not Verdict.FAIL


def test_tsql_counts_with_count_big_and_widens_avg(shop: Shop) -> None:
    if shop.name != "tsql":
        pytest.skip("T-SQL spellings")
    (count,) = Checker(shop.connector).check("COUNT orders VALUE == 100").outcomes
    (avg,) = Checker(shop.connector).check("AVG tags.value VALUE == 3.2").outcomes
    assert count.verdict is Verdict.PASS and avg.verdict is Verdict.PASS, avg.explanation
    assert count.evidence is not None and "COUNT_BIG" in (count.evidence.sql or "")
    assert avg.evidence is not None and "+ 0.0" in (avg.evidence.sql or "")


# -- ClickHouse traps ---------------------------------------------------------------------------


def test_clickhouse_left_join_subquery_uses_nulls(shop: Shop) -> None:
    (outcome,) = (
        Checker(shop.connector)
        .check("JOIN orders.customer_id -> customers.id MATCH >= 80%")
        .outcomes
    )
    assert outcome.verdict is Verdict.PASS, outcome.explanation
    # orphans 11 and 12 must stay unmatched: with type defaults instead of NULLs they would
    # read as matched to key 0 and inflate the facts
    assert outcome.facts["matched"] == 10 and outcome.facts["matched_left_rows"] == 98
    if shop.name == "clickhouse":
        assert outcome.evidence is not None and "join_use_nulls = 1" in (outcome.evidence.sql or "")


def test_value_membership_top_k_excludes_null(shop: Shop) -> None:
    plans = [
        ValueMembership(_t("tags"), Ident("label"), declared=(), report_limit=10),
        ValueMembership(_t("tags"), Ident("value"), declared=(), report_limit=10),
        ValueMembership(_t("all_null_t"), Ident("s"), declared=(), report_limit=10),
    ]
    labels, values, nulls = Checker(shop.connector).measure(plans)
    for m in (labels, values, nulls):
        assert m.error is None, (shop.name, m.error)
        assert m.payload is not None

    def observed(m: Any) -> dict[str, int]:
        rows = m.payload["rows"]
        return {str(r["value"]): int(r["freq"]) for r in rows if r["kind"] == "undeclared"}

    assert observed(labels) == {"a": 2, "b": 1, "c": 1, "c: x": 1}
    assert observed(values) == {"1": 1, "2": 1, "3": 1, "4": 1, "6": 1}
    assert observed(nulls) == {}


# -- catalog metadata ---------------------------------------------------------------------------


def test_catalog_metadata_finds_the_fixture_schema(shop: Shop) -> None:
    logged = Logged(shop.connector)
    report = Checker(logged).check("EXISTS orders COLUMN shipped_at\nTYPE orders.amount IS decimal")
    assert [o.verdict for o in report.outcomes] == [Verdict.PASS, Verdict.PASS], [
        o.explanation for o in report.outcomes
    ]
    meta = [
        p.sql_template for p in logged.executed if "information_schema" in p.sql_template.lower()
    ]
    assert meta
    if shop.name == "postgres":
        # the fixture lives in schema fl_shop, found only through the search_path
        assert any("current_schemas(false)" in sql for sql in meta), meta


_METADATA_SQL = "SELECT table_name AS tname FROM information_schema.tables WHERE table_name = {{t}}"


def test_run_metadata_reads_information_schema(shop: Shop) -> None:
    # on T-SQL this runs wrapped in a derived table (spec 9.1); a bound value, never spliced
    result = run_metadata(shop.connector, _METADATA_SQL, {"t": "orders"}, timeout_s=60)
    names = {str(next(iter(r.values()))) for r in result.rows}
    assert names == {"orders"}, result.rows
    if shop.name == "tsql":
        # the wrapped shape itself (``SELECT * FROM (<sql>) AS fl_meta_q``) runs on the view
        wrapped = f"SELECT * FROM ({_METADATA_SQL}) AS fl_meta_q"
        again = run_metadata(shop.connector, wrapped, {"t": "orders"}, timeout_s=60)
        assert [dict(r) for r in again.rows] == [dict(r) for r in result.rows]


# -- keyhash sampling (spec 19 B4) ----------------------------------------------------------------


def _keys(shop: Shop, table: str, column: str, seed: int, fraction: str) -> set[str]:
    sample = SampleSpec("keyhash", Decimal(fraction), seed, (Ident(column),))
    plan = ValueMembership(_t(table), Ident(column), declared=(), report_limit=1000, sample=sample)
    (m,) = Checker(shop.connector).measure([plan])
    assert m.error is None, (shop.name, m.error)
    assert m.payload is not None
    rows = m.payload["rows"]
    assert isinstance(rows, list)
    return {str(int(r["value"])) for r in rows if r["kind"] == "undeclared"}


def test_keyhash_keeps_identical_key_sets_on_both_sides(shop: Shop) -> None:
    left = _keys(shop, "order_items", "order_id", 7, "0.5")
    right = _keys(shop, "orders", "id", 7, "0.5")
    assert left == right, (shop.name, sorted(left ^ right))
    assert 0 < len(left) < 100  # a real sample, neither empty nor everything
    plan = KeyOverlap(
        (ColumnRef(_t("order_items"), Ident("order_id")),),
        (ColumnRef(_t("orders"), Ident("id")),),
        sample=SampleSpec("keyhash", Decimal("0.5"), 7, (Ident("order_id"),)),
        right_sample=SampleSpec("keyhash", Decimal("0.5"), 7, (Ident("id"),)),
    )
    (m,) = Checker(shop.connector).measure([plan])
    assert m.error is None and m.payload is not None, (shop.name, m.error)
    p = m.payload
    assert p["distinct_left"] == p["distinct_right"] == p["matched"] == len(left), p


def test_keyhash_same_seed_same_rows(shop: Shop) -> None:
    first = _keys(shop, "refs_parent", "k", 11, "0.1")
    again = _keys(shop, "refs_parent", "k", 11, "0.1")
    other = _keys(shop, "refs_parent", "k", 12, "0.1")
    assert first == again
    assert 30 < len(first) < 200, len(first)  # ~100 of 1000 keys
    assert first != other  # the seed is part of the hash input
