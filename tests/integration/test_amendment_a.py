"""Amendment A + B acceptance sweep (spec 18 R1-R10, 19 B1-B4; Task 27).

One test (or a small family named after it) per row of spec 18's table and per item of spec
19, each asserting that row's *Acceptance (tests)* criterion end to end. Tests that execute
are parametrised over every ``verified`` engine of ``engines.py`` through the ``shop``
fixture (DuckDB in-process; postgres, mysql, clickhouse, tsql and trino when their DSN is set
— CI requires them all through ``FACTLANG_REQUIRE_ENGINES``). Rows whose criterion is
DuckDB-only or offline (R5's fake dry-run connector, R10's exports, B4 (e)'s 1M-row table)
run on DuckDB alone.

This is the capability list the downstream SchemaGuru project depends on: a red test here
means SchemaGuru cannot rely on that row.
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import math
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from engines import ENGINE_NAMES, Style, open_engine
from expectations import CASES, COMPOSITE_JOIN, COVERAGE_FAIL, ENGINE_CASES, Case
from shop import make_shop
from test_verbs_engines import ALL_CASES as MATRIX_CASES
from test_verbs_engines import _assert_case as assert_matrix_case

import factlang as fl
from factlang.connectors import (
    DBAPIConnector,
    ExecutionError,
    QueryResult,
    check_metadata_sql,
    run_metadata,
)
from factlang.contract import plan_for
from factlang.diagnostics import DiagnosticBag
from factlang.ir import (
    REPORT_LIMIT_MAX,
    Arity,
    ColumnAggregate,
    CompositeKeyAggregate,
    KeyOverlap,
    MetadataQuery,
    Metric,
    PatternCoverage,
    ProbePlan,
    SampleSpec,
    ValueMembership,
    canonical_key,
)
from factlang.judge import Verdict
from factlang.packs.standard import standard_pack
from factlang.semantics.analyzer import analyze
from factlang.semantics.catalog import Catalog, NullCatalog
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef
from factlang.sql import JoinEdge, RenderedProbe, RenderError, get_profile, native_dialects
from factlang.sql import renderer_for as _renderer_for
from factlang.syntax import parse

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[2]


# -- fixtures --------------------------------------------------------------------------------


@dataclass
class Shop:
    name: str
    connection: Any
    style: Style
    connector: DBAPIConnector


class Logged:
    """Records every probe a connector executes (and, with ``dry_run``, every dry run)."""

    def __init__(self, inner: DBAPIConnector, dry_run: int | None = None) -> None:
        self.inner = inner
        self.dialect = inner.dialect
        self.paramstyle = inner.paramstyle
        self.executed: list[RenderedProbe] = []
        self.dry_runs: list[RenderedProbe] = []
        if dry_run is not None:

            def dry(probe: RenderedProbe) -> int:
                self.dry_runs.append(probe)
                return dry_run

            self.dry_run_bytes = dry

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


@pytest.fixture(scope="module")
def duck_conn(duck: Any) -> DBAPIConnector:
    return DBAPIConnector(duck, "duckdb")


def _t(name: str) -> TableRef:
    return TableRef(None, None, Ident(name))


def _c(table: str, column: str) -> ColumnRef:
    return ColumnRef(_t(table), Ident(column))


ORDERS, CUSTOMERS, ITEMS = _t("orders"), _t("customers"), _t("order_items")


def _sql(shop: Shop, sql: str) -> list[dict[str, Any]]:
    """Run hand-written reference SQL (no parameters) through the shop's connector."""
    probe = RenderedProbe(sql, (), Arity.ROW_SET, None, shop.name, "verified")
    return list(shop.connector.execute(probe, timeout_s=120).rows)


def _claim(text: str, catalog: Catalog | None = None) -> CheckedClaim:
    bag = DiagnosticBag()
    (claim,) = analyze(
        parse(text).document,
        standard_pack().verb_registry(),
        catalog=catalog if catalog is not None else NullCatalog(),
        bag=bag,
    )
    assert not bag.has_errors, bag.render(text)
    return claim


def _norm(payload: Mapping[str, object] | None) -> object:
    """A payload with ``server_now`` (wall clock) dropped and ROW_SET rows order-free."""
    if payload is None:
        return None
    out = {k: v for k, v in payload.items() if k != "server_now"}
    if "rows" in out:
        rows = out["rows"]
        assert isinstance(rows, list)
        out["rows"] = sorted((tuple(sorted(r.items(), key=str)) for r in rows), key=str)
    return out


def _undeclared(payload: Mapping[str, object] | None) -> list[tuple[str, int]]:
    """The ranked ``(value, freq)`` rows of a ValueMembership payload, freq desc / value asc."""
    assert payload is not None
    rows = payload["rows"]
    assert isinstance(rows, list)
    got = [(str(r["value"]), int(r["freq"])) for r in rows if r["kind"] == "undeclared"]
    return sorted(got, key=lambda vf: (-vf[1], vf[0]))


def _summary(payload: Mapping[str, object] | None) -> dict[str, Any]:
    assert payload is not None
    rows = payload["rows"]
    assert isinstance(rows, list)
    (summary,) = [r for r in rows if r["kind"] == "summary"]
    return dict(summary)


def _recording_pack(seen: dict[str, Mapping[str, object]]) -> Any:
    """The standard pack with every ``derive`` wrapped to record the payload it receives."""
    pack = standard_pack().extend("recording", "1.0")
    for contract in standard_pack().contracts():
        if contract.plan is None:
            continue

        def wrap(inner: Any) -> Any:
            def derive(payload: Mapping[str, object], claim: CheckedClaim) -> Any:
                seen[claim.semantic_hash] = payload
                return inner(payload, claim)

            return derive

        pack.override(dataclasses.replace(contract, derive=wrap(contract.derive)))
    return pack


_PLANNED = {
    "ROWS", "NULLS", "DEAD", "UNIQUE", "FANOUT", "JOIN", "VALUES", "RANGE", "PATTERN",
    "FRESH", "COUNT", "DISTINCT", "SUM", "AVG", "MIN", "MAX",
}  # fmt: skip
ALL_CASES: tuple[Case, ...] = (*CASES, *COMPOSITE_JOIN, COVERAGE_FAIL, *ENGINE_CASES)
Checker = fl.Checker


# == R1  Checker.measure / ameasure, fl.measure, Measurement =================================


def _r1_plans(shop: Shop) -> tuple[list[CheckedClaim], list[ProbePlan]]:
    texts = [c.claim for c in ALL_CASES if c.verb in _PLANNED and c.catalog == "warehouse"]
    texts.append("UNIQUE order_items.order_id WITH [line]")  # a composite key
    catalog = Checker(shop.connector).catalog
    claims = [_claim(t, catalog) for t in texts]
    plans = []
    for claim in claims:
        contract = standard_pack().get(claim.verb)
        assert contract is not None
        plans.append(plan_for(contract, claim))
    return claims, plans


def test_R1_measured_payload_equals_what_derive_receives(shop: Shop) -> None:
    claims, plans = _r1_plans(shop)
    seen: dict[str, Mapping[str, object]] = {}
    Checker(shop.connector, pack=_recording_pack(seen)).check(
        "\n".join(c.canonical_text for c in claims)
    )
    measured = fl.measure(plans, connector=shop.connector)
    kinds = set()
    for claim, plan, m in zip(claims, plans, measured, strict=True):
        assert isinstance(m, fl.Measurement)
        assert m.error is None, (shop.name, claim.canonical_text, m.error)
        assert m.plan == plan
        assert claim.semantic_hash in seen, (shop.name, claim.canonical_text)
        assert _norm(m.payload) == _norm(seen.get(claim.semantic_hash)), (
            shop.name,
            claim.canonical_text,
        )
        kinds.add(type(plan))
    # every probe primitive is exercised (MetadataQuery is the catalog's, never a claim's)
    assert kinds == {
        ColumnAggregate,
        CompositeKeyAggregate,
        ValueMembership,
        KeyOverlap,
        PatternCoverage,
    }
    # the single-plan form of the facade returns one Measurement
    one = fl.measure(plans[0], connector=shop.connector)
    assert isinstance(one, fl.Measurement) and _norm(one.payload) == _norm(measured[0].payload)


def test_R1_fused_batch_equals_unfused(shop: Shop) -> None:
    _, plans = _r1_plans(shop)
    fused_log, plain_log = Logged(shop.connector), Logged(shop.connector)
    fused = fl.measure(plans, connector=fused_log)
    plain = fl.measure(plans, connector=plain_log, fuse=False)
    assert [_norm(m.payload) for m in fused] == [_norm(m.payload) for m in plain]
    assert [m.error for m in fused] == [m.error for m in plain] == [None] * len(plans)
    assert len(fused_log.executed) < len(plain_log.executed)
    # ameasure is the same measurement
    again = asyncio.run(Checker(shop.connector).ameasure(plans))
    assert [_norm(m.payload) for m in again] == [_norm(m.payload) for m in fused]


def test_R1_failure_sets_error_and_never_raises(shop: Shop) -> None:
    missing = ColumnAggregate(_t("no_such_table"), None, (Metric.ROW_COUNT,))
    ok = ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,))
    bad, good = fl.measure([missing, ok], connector=shop.connector)
    assert bad.payload is None and bad.error is not None and bad.error.code == "FL700"
    assert bad.verdict_hint is Verdict.ERROR
    assert good.error is None and good.payload == {"row_count": 100}
    (abad,) = asyncio.run(Checker(shop.connector).ameasure([missing]))
    assert abad.error is not None and abad.error.code == "FL700"


# == R2  ValueMembership(declared=()) -> exact top-k =========================================


@pytest.mark.parametrize("k", [1, 2, 3, 10])
def test_R2_top_k_equals_group_by_reference(shop: Shop, k: int) -> None:
    plan = ValueMembership(ORDERS, Ident("status"), declared=(), report_limit=k)
    m = fl.measure(plan, connector=shop.connector)
    assert m.error is None, (shop.name, m.error)
    reference = _sql(
        shop,
        "SELECT status AS v, COUNT(*) AS n FROM orders WHERE status IS NOT NULL "
        "GROUP BY status ORDER BY n DESC, v ASC",
    )
    want = [(str(r["v"]), int(r["n"])) for r in reference][:k]
    # k = 2 cuts between churned (32) and open (32): the value-ASC tie-break decides
    assert _undeclared(m.payload) == want, (shop.name, k)
    assert _summary(m.payload)["non_null"] == 100


def test_R2_null_excluded_and_report_limit_bounds(shop: Shop) -> None:
    labels, nulls = fl.measure(
        [
            ValueMembership(_t("tags"), Ident("label"), declared=(), report_limit=REPORT_LIMIT_MAX),
            ValueMembership(_t("all_null_t"), Ident("s"), declared=(), report_limit=10),
        ],
        connector=shop.connector,
    )
    assert labels.error is None and nulls.error is None, (labels.error, nulls.error)
    assert dict(_undeclared(labels.payload)) == {"a": 2, "b": 1, "c": 1, "c: x": 1}
    assert _summary(labels.payload)["non_null"] == 5  # the NULL label is not a value
    assert _undeclared(nulls.payload) == []
    assert REPORT_LIMIT_MAX == 1000
    with pytest.raises(ValueError, match="report_limit"):
        ValueMembership(ORDERS, Ident("status"), declared=(), report_limit=1001)


def test_R2_values_in_empty_list_claim_unchanged(shop: Shop) -> None:
    report = Checker(shop.connector).check(
        "VALUES tags.label IN []\nVALUES all_null_t.s IN []\nVALUES empty_t.s IN []"
    )
    some, all_null, empty = report.outcomes
    assert some.verdict is Verdict.FAIL, some.explanation
    assert some.facts["undeclared_rows"] == 5 and some.facts["undeclared_distinct"] == 4
    assert some.facts["coverage"] == 0
    assert all_null.verdict is Verdict.NO_DATA and empty.verdict is Verdict.NO_DATA


# == R3  composite join keys =================================================================


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("JOIN a.x -> b.y WITH [c] MATCH >= 99%", "FL304"),
        ("JOIN a.x -> b.y TO [c] MATCH >= 99%", "FL304"),
        ("JOIN a.x -> b.y WITH [c, d] TO [c] MATCH >= 99%", "FL309"),
        ("JOIN a.x -> b.y WITH [c] TO [c, d] MATCH >= 99%", "FL309"),
    ],
)
def test_R3_parser_analyser_codes(text: str, code: str) -> None:
    assert code in fl.lint(text).codes(), text
    assert fl.lint("JOIN a.x -> b.y WITH [c, d] TO [e, f] MATCH >= 99%").codes() == []
    with pytest.raises(ValueError, match="length mismatch"):
        KeyOverlap((_c("a", "x"), _c("a", "c")), (_c("b", "y"),))


def test_R3_two_part_key_with_null_part_is_excluded(shop: Shop) -> None:
    # comp_left: (1,x) (1,x) (2,x) (2,y) (3,NULL); comp_right: (1,x) (2,x) (4,z)
    (outcome,) = Checker(shop.connector).check(COMPOSITE_JOIN[0].claim).outcomes
    assert outcome.verdict is Verdict.PASS, outcome.explanation
    assert outcome.facts["distinct_left"] == 3  # (3, NULL) is not a key
    assert outcome.facts["left_non_null"] == 4
    assert outcome.facts["matched"] == 2 and outcome.facts["matched_left_rows"] == 3
    m = fl.measure(
        KeyOverlap(
            (_c("comp_left", "a"), _c("comp_left", "b")),
            (_c("comp_right", "a"), _c("comp_right", "b")),
        ),
        connector=shop.connector,
    )
    assert m.error is None and m.payload is not None, m.error
    assert {k: int(v) for k, v in m.payload.items()} == {  # type: ignore[call-overload]
        "distinct_left": 3,
        "matched": 2,
        "distinct_right": 3,
        "left_non_null": 4,
        "matched_left_rows": 3,
    }


def test_R3_join_examples_at_least_three_valid_and_three_invalid() -> None:
    join = standard_pack().get("JOIN")
    assert join is not None
    valid = [e for e in join.examples if e.valid]
    invalid = [e for e in join.examples if not e.valid]
    assert len(valid) >= 3 and len(invalid) >= 3
    for e in valid:
        assert not fl.lint(e.source).has_errors, e.source
    for e in invalid:
        assert e.expect in fl.lint(e.source).codes(), e.source
    # the composite forms are among them
    assert any("WITH" in e.source and "TO" in e.source for e in valid)
    assert {e.expect for e in invalid} >= {"FL304", "FL309"}


# == R4  reverse & row facts, COVERAGE, right_filter =========================================


def test_R4_asymmetric_join_exact_numbers(shop: Shop) -> None:
    # refs_child (5 rows, keys 1,1,2,3,3) -> refs_parent (keys 1..1000)
    (outcome,) = Checker(shop.connector).check(COVERAGE_FAIL.claim).outcomes
    assert outcome.verdict is Verdict.FAIL, outcome.explanation
    facts = outcome.facts
    assert facts["match_rate"] == 1  # forward 1.0
    assert facts["coverage"] == Decimal(3) / Decimal(1000)  # reverse < 0.01
    assert facts["coverage"] < Decimal("0.01")  # type: ignore[operator]
    assert facts["row_match_rate"] == 1
    assert facts["fan_in"] == Decimal(5) / Decimal(3)
    assert facts["distinct_right"] == 1000
    assert facts["left_non_null"] == 5 and facts["matched_left_rows"] == 5
    (ok,) = (
        Checker(shop.connector)
        .check("JOIN refs_child.k -> refs_parent.k MATCH == 100% COVERAGE < 1%")
        .outcomes
    )
    assert ok.verdict is Verdict.PASS, ok.explanation


def test_R4_right_filter_and_empty_right_side_is_none(shop: Shop) -> None:
    join = standard_pack().get("JOIN")
    assert join is not None
    claim = _claim("JOIN refs_child.k -> refs_parent.k MATCH >= 1%")
    narrow, _ = fl.parse_filter("k <= 10")
    nothing, _ = fl.parse_filter("k > 100000")
    left, right = (_c("refs_child", "k"),), (_c("refs_parent", "k"),)
    m_narrow, m_none = fl.measure(
        [
            KeyOverlap(left, right, right_filter=narrow),
            KeyOverlap(left, right, right_filter=nothing),
        ],
        connector=shop.connector,
    )
    assert m_narrow.error is None and m_none.error is None, (m_narrow.error, m_none.error)
    assert m_narrow.payload is not None and m_none.payload is not None
    f_narrow = join.derive(m_narrow.payload, claim)
    assert f_narrow["distinct_right"] == 10 and f_narrow["coverage"] == Decimal(3) / 10
    assert f_narrow["match_rate"] == 1  # right_filter never touches the left side
    f_none = join.derive(m_none.payload, claim)
    assert f_none["distinct_right"] == 0
    assert f_none["coverage"] is None  # None, not 0
    # the claim surface: an empty right table
    (outcome,) = (
        Checker(shop.connector).check("JOIN customers.id -> empty_t.id MATCH >= 1%").outcomes
    )
    assert outcome.facts["distinct_right"] == 0 and outcome.facts["coverage"] is None


# == R5  Checker.estimate ====================================================================


def test_R5_estimate_through_dry_run_never_executes(duck_conn: DBAPIConnector) -> None:
    dry = Logged(duck_conn, dry_run=12345)
    plan = ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,))
    by_plan = Checker(dry).estimate([plan])
    by_text = Checker(dry).estimate("ROWS orders VALUE >= 1\nNOTE orders.id IS x BECAUSE 'y'")
    assert [(e.bytes, e.method) for e in by_plan + by_text] == [(12345, "dry_run")] * 2
    assert isinstance(by_plan[0], fl.CostEstimate) and by_plan[0].key == canonical_key(plan)
    assert by_plan[0].sql and "orders" in by_plan[0].sql
    assert len(dry.dry_runs) == 2
    assert dry.executed == []  # nothing executed

    plain = Logged(duck_conn)
    (est,) = Checker(plain).estimate([plan])
    assert est.bytes is None and est.method == "unknown"
    assert plain.executed == []


# == R6  parse_filter, render_filter, render_join, RenderedFragment ==========================

HOSTILE = "x'; DROP TABLE orders; --"


def test_R6_every_native_profile_renders_and_binds_hostile_literals() -> None:
    flt, bag = fl.parse_filter("status = 'x''; DROP TABLE orders; --' AND amount > 1")
    assert flt is not None and not bag.has_errors
    edge = EdgeRef(_c("orders", "customer_id"), _c("customers", "id"))
    for dialect in native_dialects():
        frag = fl.sql.render_filter(flt, dialect=dialect, table=ORDERS, alias="o")
        assert isinstance(frag, fl.RenderedFragment) and frag.dialect == dialect
        assert "DROP" not in frag.sql_template, dialect
        assert next(p.value for p in frag.params) == HOSTILE
        join = fl.sql.render_join([edge], dialect, filters={ORDERS: flt})
        assert isinstance(join, fl.RenderedFragment)
        assert join.sql_template.startswith("FROM "), (dialect, join.sql_template)
        assert "DROP" not in join.sql_template, dialect
        assert HOSTILE in [p.value for p in join.params]


def test_R6_executed_render_join_matches_hand_written_join(shop: Shop) -> None:
    open_, _ = fl.parse_filter("status = 'open'")
    small, _ = fl.parse_filter("id <= 5")
    assert open_ is not None and small is not None
    path = [
        JoinEdge((_c("order_items", "order_id"),), (_c("orders", "id"),)),
        EdgeRef(_c("orders", "customer_id"), _c("customers", "id")),
    ]
    frag = fl.sql.render_join(
        path,
        shop.name,
        filters={ORDERS: open_, CUSTOMERS: small},
        aliases={ITEMS: "i", ORDERS: "o", CUSTOMERS: "c"},
    )
    assert "open" not in frag.sql_template
    probe = RenderedProbe(
        "SELECT COUNT(*) AS n " + frag.sql_template,
        frag.params,
        Arity.SINGLE_ROW,
        None,
        shop.name,
        frag.tier,
    )
    (got,) = shop.connector.execute(probe, timeout_s=120).rows
    (want,) = _sql(
        shop,
        "SELECT COUNT(*) AS n FROM order_items i "
        "JOIN orders o ON i.order_id = o.id AND o.status = 'open' "
        "JOIN customers c ON o.customer_id = c.id AND c.id <= 5",
    )
    assert int(got["n"]) == int(want["n"]) > 0, (shop.name, got, want)


# == R7  native trino profile, tier verified =================================================


def _load(path: Path, name: str) -> Any:
    """Import a test module by path (test directories are not packages)."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_R7_trino_profile_is_native_verified_and_in_the_matrix() -> None:
    assert "trino" in native_dialects()
    profile = get_profile("trino")
    assert profile is not None and profile.tier == "verified"
    # the conformance suite's dialect parametrisation includes trino
    conformance = _load(ROOT / "tests" / "conformance" / "test_dialects.py", "fl_t27_dialects")
    assert "trino" in conformance.PROFILES
    by_name = [
        mark.args[1]
        for obj in vars(conformance).values()
        if callable(obj) and getattr(obj, "__name__", "").startswith("test_")
        for mark in getattr(obj, "pytestmark", [])
        if mark.name == "parametrize" and mark.args[0] == "name"
    ]
    assert by_name and all("trino" in list(values) for values in by_name), len(by_name)
    assert "trino" in ENGINE_NAMES  # the Task-20 verb matrix (and this file) run on it
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "trinodb/trino" in ci  # a Trino service container in integration CI ...
    require = next(line for line in ci.splitlines() if "FACTLANG_REQUIRE_ENGINES:" in line)
    assert "trino" in require  # ... which may not be skipped there


def test_R7_verb_matrix_runs_on_trino(shop: Shop) -> None:
    if shop.name != "trino":
        pytest.skip("the Trino row of the matrix")
    for case in MATRIX_CASES:  # the Task-20 matrix and its own assertion helper
        checker = Checker(shop.connector, catalog=NullCatalog() if case.catalog == "null" else None)
        (outcome,) = checker.check(case.claim).outcomes
        assert_matrix_case(outcome, case, shop.name)
        if outcome.verdict in (Verdict.PASS, Verdict.FAIL, Verdict.NO_DATA):
            assert outcome.evidence is not None and outcome.evidence.tier == "verified"


# == R8  fl.connectors.run_metadata, FL703 ===================================================

REFUSED = (
    "DELETE FROM orders",
    "UPDATE orders SET status = 'x'",
    "INSERT INTO orders (id) VALUES (1)",
    "DROP TABLE orders",
    "CREATE TABLE fl_x (a INTEGER)",
    "ALTER TABLE orders ADD COLUMN z INTEGER",
    "TRUNCATE TABLE orders",
    "SELECT 1; DROP TABLE orders",
    "SELECT 1 AS a; SELECT 2 AS b",
)


@pytest.mark.parametrize("sql", REFUSED)
def test_R8_guard_rejects_dml_ddl_and_multi_statement(shop: Shop, sql: str) -> None:
    logged = Logged(shop.connector)
    with pytest.raises(ExecutionError) as info:
        fl.connectors.run_metadata(logged, sql)
    assert info.value.code == "FL703", (shop.name, sql, str(info.value))
    assert logged.executed == []  # refused before anything executes


def test_R8_information_schema_select_works(shop: Shop) -> None:
    result = run_metadata(
        shop.connector,
        "SELECT table_name AS tname FROM information_schema.tables WHERE table_name = {{t}}",
        {"t": "orders"},
        timeout_s=60,
    )
    assert {str(next(iter(r.values()))) for r in result.rows} == {"orders"}


def test_R8_show_tables_allowed(shop: Shop) -> None:
    if shop.name == "tsql":
        # spec 9.1 (run_metadata on T-SQL): statements need no separator there, so the guard
        # admits only a SELECT, which runs wrapped as a derived table; SHOW is refused
        with pytest.raises(ExecutionError, match="FL703"):
            check_metadata_sql("SHOW TABLES", "tsql")
        return
    check_metadata_sql("SHOW TABLES", shop.name)  # the guard admits it
    if shop.name == "postgres":
        return  # PostgreSQL has no SHOW TABLES statement; the guard is what is under test
    result = run_metadata(shop.connector, "SHOW TABLES", timeout_s=60)
    names = {str(next(iter(r.values()))) for r in result.rows}
    assert {"orders", "customers"} <= names, (shop.name, sorted(names))


def test_R8_fl703_is_documented() -> None:
    wiki = (ROOT / "docs" / "wiki" / "Diagnostics.md").read_text(encoding="utf-8")
    assert "FL703" in wiki
    reference = ROOT / "docs" / "reference" / "diagnostics.md"
    if reference.exists():  # created by the docs task
        assert "FL703" in reference.read_text(encoding="utf-8")


# == R9  bare boolean filter atom ============================================================


def test_R9_grammar_conformance_blocks_carry_the_bare_atom() -> None:
    grammar = (ROOT / "docs" / "reference" / "grammar.md").read_text(encoding="utf-8")
    for block in ("ROWS orders WHERE deleted", "ROWS orders WHERE deleted IS NULL OR NOT deleted"):
        assert f"```factlang-valid\n{block}\n```" in grammar
        assert not parse(block).diagnostics.has_errors, block


def test_R9_bare_atom_hashes_and_measures_like_equals_true(shop: Shop) -> None:
    # tags.deleted: TRUE x2, FALSE x3, NULL x1
    report = Checker(shop.connector).check(
        "ROWS tags VALUE == 2 WHERE deleted\n"
        "ROWS tags VALUE == 2 WHERE deleted = TRUE\n"
        "ROWS tags VALUE == 3 WHERE NOT deleted\n"
        "ROWS tags VALUE == 3 WHERE NOT deleted = TRUE\n"
        "ROWS tags VALUE == 3 WHERE deleted = FALSE"
    )
    bare, explicit, not_bare, not_explicit, false = report.outcomes
    assert [o.verdict for o in report.outcomes] == [Verdict.PASS] * 5, [
        (o.claim_text, o.explanation) for o in report.outcomes
    ]
    assert bare.semantic_hash == explicit.semantic_hash
    assert not_bare.semantic_hash == not_explicit.semantic_hash
    assert bare.semantic_hash != false.semantic_hash  # negative control


def test_R9_fl305_on_non_boolean_column_when_catalog_knows(shop: Shop) -> None:
    (outcome,) = Checker(shop.connector).check("ROWS tags VALUE >= 1 WHERE label").outcomes
    assert outcome.verdict is Verdict.INVALID
    assert "FL305" in [d["code"] for d in outcome.diagnostics]
    # without catalog knowledge it is accepted (and the boolean column runs)
    (unknown,) = (
        Checker(shop.connector, catalog=NullCatalog())
        .check("ROWS tags VALUE == 2 WHERE deleted")
        .outcomes
    )
    assert unknown.verdict is Verdict.PASS, unknown.explanation


# == R10 public-API stability promise ========================================================

R10_NAMES = (
    "measure",
    "Measurement",
    "CostEstimate",
    "parse_filter",
    "RenderedFragment",
    "run_metadata",
    "connectors",
    "sql",
    "ir",
)
R10_CODE_SPANS = (
    "fl.measure",
    "Checker.estimate",
    "fl.parse_filter",
    "fl.sql.render_filter",
    "fl.sql.render_join",
    "fl.connectors.run_metadata",
)


def test_R10_exports_are_pinned_exactly() -> None:
    from factlang import connectors, engine, ir, sql

    api_test = _load(ROOT / "tests" / "unit" / "test_api.py", "fl_t27_api")
    assert set(fl.__all__) == api_test.EXPORTS  # the unit test pins the exact list
    assert len(fl.__all__) == len(set(fl.__all__))
    assert set(R10_NAMES) <= api_test.EXPORTS
    assert fl.measure.__module__ == "factlang.api"
    assert fl.Measurement is engine.Measurement and fl.CostEstimate is engine.CostEstimate
    assert fl.Checker is engine.Checker
    for method in ("measure", "ameasure", "estimate"):
        assert callable(getattr(fl.Checker, method)), method
    assert fl.RenderedFragment is sql.RenderedFragment
    assert fl.sql is sql and fl.sql.render_filter is sql.render_filter
    assert fl.sql.render_join is sql.render_join
    assert fl.connectors is connectors and fl.run_metadata is connectors.run_metadata
    assert fl.connectors.run_metadata is connectors.run_metadata
    assert fl.ir is ir
    for name in ("SampleSpec", "ValueMembership", "KeyOverlap", "MetadataQuery"):
        assert name in ir.__all__, name


def test_R10_api_docs_list_them_with_the_stability_promise() -> None:
    api = (ROOT / "docs" / "reference" / "api.md").read_text(encoding="utf-8")
    wiki = (ROOT / "docs" / "wiki" / "Measurement-API.md").read_text(encoding="utf-8")
    for text in (api, wiki):
        assert "SemVer" in text
    assert "Stable API" in api
    for span in R10_CODE_SPANS:
        assert re.search(r"`" + re.escape(span) + r"[`(]", api), span  # a code span
    for name in (
        "right_filter",
        "report_limit",
        "SampleSpec",
        "RenderedFragment",
        "CostEstimate",
        "FL703",
        "FL404",
        "FL405",
        "FL506",
    ):
        assert name in wiki, name


# == B1  SampleSpec (IR) =====================================================================


def test_B1_sample_spec_and_primitive_fields() -> None:
    key = (Ident("id"),)
    spec = SampleSpec("keyhash", Decimal("0.01"), 7, key)
    assert (spec.method, spec.fraction, spec.seed, spec.key) == ("keyhash", Decimal("0.01"), 7, key)
    assert SampleSpec("bernoulli", Decimal("0.5")).seed == 0
    for bad in (Decimal(0), Decimal("1.5"), Decimal(-1)):
        with pytest.raises(ValueError):
            SampleSpec("bernoulli", bad)
    with pytest.raises(ValueError, match="key"):
        SampleSpec("keyhash", Decimal("0.5"))
    with pytest.raises(ValueError, match="key"):
        SampleSpec("system", Decimal("0.5"), key=key)
    for cls in (
        ColumnAggregate,
        CompositeKeyAggregate,
        ValueMembership,
        KeyOverlap,
        PatternCoverage,
    ):
        fields = {f.name: f.default for f in dataclasses.fields(cls)}
        assert fields["sample"] is None, cls
    assert {f.name for f in dataclasses.fields(KeyOverlap)} >= {"sample", "right_sample"}
    assert "sample" not in {f.name for f in dataclasses.fields(MetadataQuery)}
    # the sample is part of a plan's identity
    exact = ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,))
    assert canonical_key(exact) != canonical_key(dataclasses.replace(exact, sample=spec))


@pytest.mark.parametrize("method", ["system", "bernoulli"])
def test_B1_fl405_block_and_row_samples_need_a_single_read(
    duck_conn: DBAPIConnector, method: str
) -> None:
    rows = SampleSpec(method, Decimal("0.5"), 1)  # type: ignore[arg-type]
    multi_read = [
        ColumnAggregate(ORDERS, Ident("id"), (Metric.MAX_PER_KEY,), sample=rows),
        ColumnAggregate(ORDERS, Ident("id"), (Metric.DUPLICATE_KEYS,), sample=rows),
        CompositeKeyAggregate(
            ITEMS, (Ident("order_id"), Ident("line")), (Metric.DISTINCT_KEYS,), sample=rows
        ),
        ValueMembership(ORDERS, Ident("status"), declared=(), sample=rows),
        KeyOverlap((_c("orders", "customer_id"),), (_c("customers", "id"),), sample=rows),
    ]
    for m in fl.measure(multi_read, connector=duck_conn):
        assert m.error is not None and m.error.code == "FL405", (m.plan, m.error)
        assert m.payload is None
    single = ColumnAggregate(ORDERS, Ident("id"), (Metric.ROW_COUNT,), sample=rows)
    (ok,) = fl.measure([single], connector=duck_conn)
    if method == "system":  # a single read passes planning; DuckDB declares no SYSTEM style
        assert ok.error is not None and ok.error.code == "FL506", ok.error
    else:
        assert ok.error is None, ok.error

    # keyhash is deterministic and allowed everywhere
    def kh(column: str) -> SampleSpec:
        return SampleSpec("keyhash", Decimal("0.5"), 1, (Ident(column),))

    rekeyed = [
        dataclasses.replace(multi_read[0], sample=kh("id")),
        dataclasses.replace(multi_read[1], sample=kh("id")),
        dataclasses.replace(multi_read[2], sample=kh("order_id")),
        dataclasses.replace(multi_read[3], sample=kh("status")),
        dataclasses.replace(multi_read[4], sample=kh("customer_id"), right_sample=kh("id")),
    ]
    for m in fl.measure(rekeyed, connector=duck_conn):
        assert m.error is None, (m.plan, m.error)


# == B2  rendering ===========================================================================

# spec 19 keyhash ruling: the hash each profile must use, and whether h is signed (folded
# with MOD(MOD(h, B) + B, B), never ABS)
KEYHASH_TOKENS: dict[str, tuple[tuple[str, ...], bool]] = {
    "bigquery": (("FARM_FINGERPRINT(",), True),
    "snowflake": (("HASH(",), True),
    "databricks": (("xxhash64(",), True),
    "duckdb": (("hash(",), False),
    "clickhouse": (("cityHash64(",), False),
    "postgres": (("hashtextextended(",), True),
    "redshift": (("STRTOL(SUBSTRING(MD5(",), False),
    "mysql": (("CONV(SUBSTRING(MD5(",), False),
    "tsql": (("HASHBYTES('MD5', CAST(", "AS NVARCHAR(4000))"), True),
    "oracle": (("ORA_HASH(", "4294967295, 0)"), False),
    "trino": (("from_big_endian_64(xxhash64(to_utf8(",), True),
}
SAMPLE_TEXT = ("TABLESAMPLE", "SAMPLE ", "1000000", "|")
FOLDED = re.compile(
    r"MOD\(MOD\(.+, 1000000\) \+ 1000000, 1000000\)"  # MOD(MOD(h, B) + B, B)
    r"|\(\(.+ % 1000000 \+ 1000000\) % 1000000\)"  # ((h % B + B) % B)
)
UNFOLDED = re.compile(r"MOD\(.+, 1000000\)|% 1000000\)")


def test_B2_keyhash_excludes_null_key_parts_and_rounds_half_up() -> None:
    key = (Ident("id"), Ident("customer_id"))
    for dialect in native_dialects():
        render = _renderer_for(dialect)
        spec = SampleSpec("keyhash", Decimal("0.5"), 1, key)
        sql = render(ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,), sample=spec)).sql_template
        for column in ("id", "customer_id"):
            assert re.search(rf"\b{column}\W? IS NOT NULL", sql), (dialect, column, sql)
        for fraction, threshold in (("0.0000005", 1), ("0.0000015", 2), ("0.0000025", 3)):
            spec = SampleSpec("keyhash", Decimal(fraction), 1, key[:1])
            probe = render(ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,), sample=spec))
            ints = [p.value for p in probe.params if p.family == "integer"]
            assert ints == [threshold], (dialect, fraction, ints)  # ROUND half-up, not even


def test_B2_keyhash_predicate_per_profile() -> None:
    assert set(native_dialects()) == set(KEYHASH_TOKENS)  # a new profile must be ruled on
    spec = SampleSpec("keyhash", Decimal("0.25"), 42, (Ident("id"),))
    plan = ColumnAggregate(ORDERS, Ident("id"), (Metric.ROW_COUNT,), sample=spec)
    for dialect, (tokens, signed) in KEYHASH_TOKENS.items():
        probe = _renderer_for(dialect)(plan)
        sql = probe.sql_template
        for token in tokens:
            assert token in sql, (dialect, token, sql)
        assert "ABS(" not in sql.upper(), dialect
        if signed:  # MOD(MOD(h, B) + B, B); T-SQL spells MOD as %
            assert FOLDED.search(sql), (dialect, sql)
        else:  # h is non-negative: one MOD, no fold
            assert "+ 1000000" not in sql and not FOLDED.search(sql), (dialect, sql)
            assert UNFOLDED.search(sql), (dialect, sql)
        values = [p.value for p in probe.params]
        assert "42" in values, (dialect, values)  # the seed, bound as a string
        assert 250000 in values, (dialect, values)  # fraction x 1e6, bound as an integer


def test_B2_methods_render_or_fl506_and_fraction_one_renders_no_sample() -> None:
    exact = ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,))
    for dialect in native_dialects():
        render = _renderer_for(dialect)
        profile = get_profile(dialect)
        assert profile is not None
        plain = render(exact).sql_template
        assert not any(t in plain for t in SAMPLE_TEXT), (dialect, plain)  # exact: no sample
        for method in ("system", "bernoulli"):
            spec = SampleSpec(method, Decimal("0.3"), 5)  # type: ignore[arg-type]
            sampled = dataclasses.replace(exact, sample=spec)
            if profile.sample_styles.get(method) is None:
                with pytest.raises(RenderError, match=r"^FL506"):
                    render(sampled)
            else:
                assert render(sampled).sql_template != plain, (dialect, method)
            whole = dataclasses.replace(exact, sample=SampleSpec(method, Decimal(1), 5))  # type: ignore[arg-type]
            text = render(whole).sql_template
            assert not any(t in text for t in SAMPLE_TEXT), (dialect, method, text)
        whole_key = dataclasses.replace(
            exact, sample=SampleSpec("keyhash", Decimal(1), 5, (Ident("id"),))
        )
        assert "1000000" not in render(whole_key).sql_template, dialect


def test_B2_sqlglot_bridge_keyhash_is_fl506_off_the_ruled_dialects() -> None:
    import sqlglot  # a dev dependency: its absence is a failure, never a skip

    assert sqlglot.__version__
    spec = SampleSpec("keyhash", Decimal("0.5"), 1, (Ident("id"),))
    plan = ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,), sample=spec)
    with pytest.raises(RenderError, match=r"^FL506"):
        _renderer_for("sqlite", prefer="sqlglot")(plan)


# == B3  results =============================================================================


def test_B3_sampled_evidence_method_and_raw_payload(shop: Shop) -> None:
    half = SampleSpec("keyhash", Decimal("0.5"), 7, (Ident("id"),))
    exact = ColumnAggregate(ORDERS, Ident("id"), (Metric.ROW_COUNT, Metric.NON_NULL))
    sampled = dataclasses.replace(exact, sample=half)
    m_exact, m_sampled = fl.measure([exact, sampled], connector=shop.connector)
    assert m_exact.evidence is not None and m_exact.evidence.method == "exact"
    assert m_exact.evidence.sample is None and "sampled_rows" not in (m_exact.payload or {})
    assert m_sampled.error is None and m_sampled.payload is not None, m_sampled.error
    assert m_sampled.evidence is not None and m_sampled.evidence.sample == half
    assert m_sampled.evidence.method == "sampled:keyhash:0.5:7"
    p = m_sampled.payload
    # raw metrics on the sampled rows, never scaled up to the table
    assert p["sampled_rows"] == p["row_count"] == p["non_null"]
    assert 0 < int(p["row_count"]) < 100  # type: ignore[call-overload]

    overlap = KeyOverlap(
        (_c("orders", "customer_id"),),
        (_c("customers", "id"),),
        sample=SampleSpec("keyhash", Decimal(1), 3, (Ident("customer_id"),)),
        right_sample=SampleSpec("keyhash", Decimal(1), 3, (Ident("id"),)),
    )
    m = fl.measure(overlap, connector=shop.connector)
    assert m.error is None and m.payload is not None, m.error
    assert m.payload["sampled_left_rows"] == 100 and m.payload["sampled_right_rows"] == 10


def test_B3_estimate_prices_sampled_plans(duck_conn: DBAPIConnector) -> None:
    dry = Logged(duck_conn, dry_run=777)
    plan = ColumnAggregate(
        ORDERS, None, (Metric.ROW_COUNT,), sample=SampleSpec("bernoulli", Decimal("0.1"), 2)
    )
    (est,) = Checker(dry).estimate([plan])
    assert (est.bytes, est.method) == (777, "dry_run")
    assert est.sql is not None and "TABLESAMPLE" in est.sql
    assert dry.executed == []


# == B4  guards and execution ================================================================


def test_B4_check_rejects_a_sampled_plan_fl404(duck_conn: DBAPIConnector) -> None:
    pack = standard_pack().extend("sampling", "1.0")
    rows, join = pack.get("ROWS"), pack.get("JOIN")
    assert rows is not None and join is not None

    def kh(column: str) -> SampleSpec:
        return SampleSpec("keyhash", Decimal("0.5"), 1, (Ident(column),))

    def sampled_rows(claim: CheckedClaim) -> ProbePlan:
        return ColumnAggregate(claim.table, None, (Metric.ROW_COUNT,), sample=kh("id"))

    def sampled_join(claim: CheckedClaim) -> ProbePlan:
        plan = join.plan(claim)  # type: ignore[misc]
        assert isinstance(plan, KeyOverlap)
        return dataclasses.replace(plan, right_sample=kh("id"))

    pack.override(dataclasses.replace(rows, plan=sampled_rows))
    pack.override(dataclasses.replace(join, plan=sampled_join))
    logged = Logged(duck_conn)
    report = Checker(logged, pack=pack, catalog=NullCatalog()).check(
        "ROWS orders VALUE >= 1\nJOIN orders.customer_id -> customers.id MATCH >= 1%"
    )
    for outcome in report.outcomes:
        assert outcome.verdict is Verdict.ERROR, (outcome.verb, outcome.explanation)
        assert outcome.explanation.startswith("FL404"), outcome.explanation
    assert [o.verb for o in report.outcomes] == ["ROWS", "JOIN"]
    assert logged.executed == []


def _kept(shop: Shop, table: str, column: str, seed: int, fraction: str) -> dict[str, int]:
    """Kept key -> rows kept for it (keyhash on ``column``), through an exact top-k."""
    sample = SampleSpec("keyhash", Decimal(fraction), seed, (Ident(column),))
    plan = ValueMembership(_t(table), Ident(column), declared=(), report_limit=1000, sample=sample)
    m = fl.measure(plan, connector=shop.connector)
    assert m.error is None, (shop.name, m.error)
    return {str(int(v)): f for v, f in _undeclared(m.payload)}


def test_B4_a_keyhash_both_sides_keep_identical_key_sets(shop: Shop) -> None:
    left = set(_kept(shop, "order_items", "order_id", 7, "0.5"))
    right = set(_kept(shop, "orders", "id", 7, "0.5"))
    assert left == right, (shop.name, sorted(left ^ right))
    assert 0 < len(left) < 100
    # a KeyOverlap under real keyhash sampling, same seed and fraction on both sides
    plan = KeyOverlap(
        (_c("order_items", "order_id"),),
        (_c("orders", "id"),),
        sample=SampleSpec("keyhash", Decimal("0.5"), 7, (Ident("order_id"),)),
        right_sample=SampleSpec("keyhash", Decimal("0.5"), 7, (Ident("id"),)),
    )
    m = fl.measure(plan, connector=shop.connector)
    assert m.error is None and m.payload is not None, (shop.name, m.error)
    p = {k: int(v) for k, v in m.payload.items()}  # type: ignore[call-overload]
    assert p["distinct_left"] == p["distinct_right"] == p["matched"] == len(right), p
    assert 0 < p["sampled_right_rows"] < 100, p  # orders has 100 rows
    lines = _sql(shop, "SELECT order_id AS k, COUNT(*) AS n FROM order_items GROUP BY order_id")
    kept_rows = sum(int(r["n"]) for r in lines if str(int(r["k"])) in right)
    assert p["sampled_left_rows"] == kept_rows, (p, kept_rows)
    assert p["sampled_right_rows"] == len(right)  # orders.id is unique


def test_B4_b_keyhash_keeps_every_row_of_a_kept_key(shop: Shop) -> None:
    whole = _kept(shop, "order_items", "order_id", 0, "1")
    part = _kept(shop, "order_items", "order_id", 3, "0.4")
    assert 0 < len(part) < len(whole) == 100
    assert {k: whole[k] for k in part} == part  # each kept key with all of its lines


def test_B4_c_same_seed_same_sample(shop: Shop) -> None:
    first = _kept(shop, "refs_parent", "k", 11, "0.1")
    assert first == _kept(shop, "refs_parent", "k", 11, "0.1")
    assert first != _kept(shop, "refs_parent", "k", 12, "0.1")


def test_B4_d_fraction_one_is_identical_to_exact(shop: Shop) -> None:
    exact = ColumnAggregate(
        ORDERS, Ident("amount"), (Metric.ROW_COUNT, Metric.SUM, Metric.MAX_VALUE)
    )
    profile = get_profile(shop.name)
    assert profile is not None
    specs = [SampleSpec("keyhash", Decimal(1), 9, (Ident("id"),))]
    specs += [
        SampleSpec(m, Decimal(1), 9)  # type: ignore[arg-type]
        for m in ("system", "bernoulli")
        if profile.sample_styles.get(m) is not None
    ]
    plans: list[ProbePlan] = [exact, *(dataclasses.replace(exact, sample=s) for s in specs)]
    base, *sampled = fl.measure(plans, connector=shop.connector)
    assert base.payload is not None
    for m in sampled:
        assert m.error is None, m.error
        assert m.payload == {**base.payload, "sampled_rows": 100}, (shop.name, m.plan)


N_BIG = 1_000_000


def _big_table(shop: Shop) -> tuple[TableRef, int]:
    """A 1M-row table on ``shop`` and the rows per sampling unit (block) of ``system``."""
    if shop.name == "duckdb":
        shop.connection.execute(
            "CREATE OR REPLACE TABLE fl_big AS SELECT range AS id FROM range(1000000)"
        )
        return _t("fl_big"), 1
    if shop.name == "postgres":
        shop.connection.execute("DROP TABLE IF EXISTS fl_big")
        shop.connection.execute(
            "CREATE TABLE fl_big AS SELECT g AS id FROM generate_series(1, 1000000) AS g"
        )
        shop.connection.execute("ANALYZE fl_big")
        (pages,) = shop.connection.execute(
            "SELECT relpages FROM pg_class WHERE relname = 'fl_big'"
        ).fetchone()
        return _t("fl_big"), math.ceil(N_BIG / int(pages))
    pytest.skip(f"no 1M-row table recipe for {shop.name}")


def test_B4_e_system_and_bernoulli_within_binomial_tolerance(shop: Shop) -> None:
    profile = get_profile(shop.name)
    assert profile is not None
    declared = [m for m in ("system", "bernoulli") if profile.sample_styles.get(m) is not None]
    for method in ("system", "bernoulli"):
        if method in declared:
            continue
        spec = SampleSpec(method, Decimal("0.3"), 17)  # type: ignore[arg-type]
        (m,) = fl.measure(
            [ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,), sample=spec)],
            connector=shop.connector,
        )
        assert m.error is not None and m.error.code == "FL506", (shop.name, method, m.error)
    if not declared:
        return
    table, rows_per_block = _big_table(shop)
    fraction = Decimal("0.3")
    for method in declared:
        spec = SampleSpec(method, fraction, 17)  # type: ignore[arg-type]
        plan = ColumnAggregate(table, None, (Metric.ROW_COUNT,), sample=spec)
        (m,) = fl.measure([plan], connector=shop.connector)
        assert m.error is None and m.payload is not None, (shop.name, method, m.error)
        got = int(m.payload["row_count"])  # type: ignore[call-overload]
        assert got == m.payload["sampled_rows"]
        block = rows_per_block if method == "system" else 1  # bernoulli: one trial per row
        units, p = N_BIG / block, float(fraction)
        sd_rows = math.sqrt(units * p * (1 - p)) * block
        assert abs(got - N_BIG * p) <= 6 * sd_rows, (shop.name, method, got, sd_rows)
        (again,) = fl.measure([plan], connector=shop.connector)
        assert again.payload == m.payload  # same seed, same sample
