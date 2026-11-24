"""The execution matrix on DuckDB: every standard verb end to end through ``Checker``
(spec 6, 7, 9.3, 18 R1-R5, 19 B3-B4)."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

import pytest
from expectations import CASES, COMPOSITE_JOIN, COVERAGE_FAIL, Case

from factlang.connectors import DBAPIConnector, ExecutionError, QueryResult
from factlang.contract import plan_for
from factlang.diagnostics import DiagnosticBag
from factlang.engine import Checker, Measurement
from factlang.ir import (
    ColumnAggregate,
    CompositeKeyAggregate,
    KeyOverlap,
    Metric,
    PatternCoverage,
    ProbePlan,
    SampleSpec,
    ValueMembership,
    canonical_key,
)
from factlang.judge import Outcome, Verdict
from factlang.optimizer import FusedAggregate, WorkItem, optimize
from factlang.packs.standard import standard_pack
from factlang.semantics.analyzer import analyze
from factlang.semantics.catalog import NullCatalog
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.refs import ColumnRef, Ident, TableRef
from factlang.sql import RenderedProbe
from factlang.syntax import parse

pytest.importorskip("duckdb")


def _t(name: str, quoted: bool = False) -> TableRef:
    return TableRef(None, None, Ident(name, quoted))


ORDERS, ITEMS, CUSTOMERS = _t("orders"), _t("order_items"), _t("customers")


class Logged:
    """A connector that records every probe it executes (and, optionally, dry-runs)."""

    def __init__(self, inner: DBAPIConnector, dry_run: int | None = None) -> None:
        self.inner = inner
        self.dialect = inner.dialect
        self.paramstyle = inner.paramstyle
        self.executed: list[RenderedProbe] = []
        self.dry_runs: list[RenderedProbe] = []
        if dry_run is not None:
            self.dry_run_value = dry_run

            def dry(probe: RenderedProbe) -> int:
                self.dry_runs.append(probe)
                return self.dry_run_value

            self.dry_run_bytes = dry

    def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
        self.executed.append(probe)
        return self.inner.execute(probe, timeout_s=timeout_s)


@pytest.fixture(scope="module")
def conn(duck: Any) -> DBAPIConnector:
    return DBAPIConnector(duck, "duckdb")


def _checker(conn: DBAPIConnector, case: Case | None = None, **kw: Any) -> Checker:
    if case is not None and case.catalog == "null":
        kw.setdefault("catalog", NullCatalog())
    return Checker(conn, **kw)


def _assert_case(outcome: Outcome, case: Case) -> None:
    assert outcome.verdict is case.verdict, (case.claim, outcome.explanation, dict(outcome.facts))
    assert outcome.verb == case.verb
    for name, want in case.facts.items():
        assert name in outcome.facts, (case.claim, name, dict(outcome.facts))
        got = outcome.facts[name]
        assert want == got, (case.claim, name, got, want)
        if isinstance(want, Decimal) and isinstance(got, Decimal):
            assert got == want  # exact, no float round trip


# -- every standard verb: PASS / FAIL / NO_DATA with exact facts ------------------------------


@pytest.mark.parametrize("case", CASES, ids=[c.id for c in CASES])
def test_every_standard_verb(conn: DBAPIConnector, case: Case) -> None:
    report = _checker(conn, case).check(case.claim)
    (outcome,) = report.outcomes
    _assert_case(outcome, case)
    if outcome.verdict in (Verdict.PASS, Verdict.FAIL, Verdict.NO_DATA):
        assert outcome.evidence is not None
        assert outcome.evidence.dialect == "duckdb"
        assert outcome.evidence.tier == "verified"
        if case.verb not in ("EXISTS", "TYPE"):
            assert outcome.evidence.sql and outcome.evidence.method == "exact"


def test_every_standard_verb_is_covered() -> None:
    verbs = set(standard_pack().names())
    assert {c.verb for c in CASES} == verbs
    for verb in verbs - {"NOTE"}:
        kinds = {c.kind for c in CASES if c.verb == verb}
        assert {"pass", "fail"} <= kinds and kinds & {"no_data", "empty"}, verb


@pytest.mark.parametrize("case", COMPOSITE_JOIN, ids=[c.id for c in COMPOSITE_JOIN])
def test_composite_join(conn: DBAPIConnector, case: Case) -> None:
    (outcome,) = Checker(conn).check(case.claim).outcomes
    _assert_case(outcome, case)


def test_coverage_fails_on_the_asymmetric_pair(conn: DBAPIConnector) -> None:
    (outcome,) = Checker(conn).check(COVERAGE_FAIL.claim).outcomes
    _assert_case(outcome, COVERAGE_FAIL)
    assert "coverage" in outcome.explanation


def test_one_batch_keeps_source_order(conn: DBAPIConnector) -> None:
    cases = [c for c in CASES if c.catalog == "warehouse"]
    report = Checker(conn).check("\n".join(c.claim for c in cases))
    assert len(report.outcomes) == len(cases)
    for outcome, case in zip(report.outcomes, cases, strict=True):
        assert outcome.claim_text == case.claim
        _assert_case(outcome, case)
    assert not report.ok  # FAIL cases are in the batch
    summary = report.summary()
    assert summary[Verdict.FAIL] == sum(c.verdict is Verdict.FAIL for c in cases)


# -- review focus ---------------------------------------------------------------------------


def test_empty_and_all_null_are_never_pass(conn: DBAPIConnector) -> None:
    for case in CASES:
        if case.kind == "no_data":
            (outcome,) = _checker(conn, case).check(case.claim).outcomes
            assert outcome.verdict is Verdict.NO_DATA, case.claim


def test_sum_decimal_column(conn: DBAPIConnector) -> None:
    (outcome,) = Checker(conn).check("SUM order_items.price VALUE == 366.30").outcomes
    value = outcome.facts["value"]
    assert isinstance(value, Decimal) and value == Decimal("366.30")
    assert outcome.verdict is Verdict.PASS
    # one cent off is a FAIL: no float tolerance sneaks in
    (off,) = Checker(conn).check("SUM order_items.price VALUE == 366.29").outcomes
    assert off.verdict is Verdict.FAIL


def test_hostile_literal_is_bound(duck: Any, conn: DBAPIConnector) -> None:
    hostile = "x''; DROP TABLE orders; --"
    logged = Logged(conn)
    report = Checker(logged, catalog=NullCatalog()).check(
        f"COUNT orders VALUE == 0 WHERE status = '{hostile}'"
    )
    (outcome,) = report.outcomes
    assert outcome.verdict is Verdict.PASS, outcome.explanation
    assert outcome.facts["value"] == 0
    probe = logged.executed[-1]
    assert "DROP" not in probe.sql_template
    assert [p.value for p in probe.params] == ["x'; DROP TABLE orders; --"]
    assert outcome.evidence is not None and "'x''; DROP TABLE orders; --'" in outcome.evidence.sql
    assert outcome.evidence.params == ("x'; DROP TABLE orders; --",)
    assert duck.execute("SELECT COUNT(*) FROM orders").fetchone() == (100,)


def test_backslash_literal_judges_correctly_with_placeholder_evidence() -> None:
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    con.execute("CREATE TABLE orders (id INTEGER, status VARCHAR)")
    con.execute("INSERT INTO orders VALUES (1, ?), (2, ?), (3, 'paid')", ["a\\b", "x\\' OR 1=1 --"])
    checker = Checker(DBAPIConnector(con, "duckdb"), catalog=NullCatalog())
    one, two, none = checker.check(
        "COUNT orders VALUE == 1 WHERE status = 'a\\b'\n"
        "COUNT orders VALUE == 1 WHERE status = 'x\\'' OR 1=1 --'\n"
        "COUNT orders VALUE == 0 WHERE status = 'a\\'"
    ).outcomes
    for outcome, value in ((one, "a\\b"), (two, "x\\' OR 1=1 --"), (none, "a\\")):
        assert outcome.verdict is Verdict.PASS, outcome.explanation
        assert outcome.evidence is not None and outcome.evidence.sql is not None
        assert "{{p1}}" in outcome.evidence.sql and "OR 1=1" not in outcome.evidence.sql
        assert outcome.evidence.params == (value,)
    con.close()


def test_quoted_table_and_reserved_column(conn: DBAPIConnector) -> None:
    text = (
        'ROWS "Order Items" VALUE == 3\n'
        'UNIQUE "Order Items".order\n'
        'SUM "Order Items".order VALUE == 6\n'
        'EXISTS "Order Items" COLUMN order\n'
        'TYPE "Order Items".order IS integer'
    )
    report = Checker(conn).check(text)
    assert [o.verdict for o in report.outcomes] == [Verdict.PASS] * 5, [
        o.explanation for o in report.outcomes
    ]
    assert report.ok


# -- fusion parity --------------------------------------------------------------------------


def _comparable(o: Outcome) -> tuple[object, ...]:
    facts = dict(o.facts)
    if o.verb == "FRESH":  # wall clock: the two runs see different server_now values
        facts.pop("age_seconds", None)
        return (o.claim_text, o.verdict, tuple(sorted(facts.items())))
    return (o.claim_text, o.verdict, o.explanation, tuple(sorted(facts.items(), key=str)))


def test_fused_and_unfused_outcomes_are_identical(conn: DBAPIConnector) -> None:
    claims = [c.claim for c in (*CASES, *COMPOSITE_JOIN, COVERAGE_FAIL)]
    claims += [
        "NULLS orders.amount RATE <= 0%",
        "MIN orders.amount VALUE == 1.25",
        "UNIQUE order_items.order_id WITH [line]",
        "FANOUT order_items.order_id WITH [line] MAX_PER_KEY <= 1",
    ]
    text = "\n".join(claims)
    fused_conn, plain_conn = Logged(conn), Logged(conn)
    fused = Checker(fused_conn).check(text)
    plain = Checker(plain_conn, fuse=False).check(text)
    fused_conn.executed = [
        p for p in fused_conn.executed if "information_schema" not in p.sql_template.lower()
    ]
    plain_conn.executed = [
        p for p in plain_conn.executed if "information_schema" not in p.sql_template.lower()
    ]
    assert [_comparable(o) for o in fused.outcomes] == [_comparable(o) for o in plain.outcomes]
    assert len(fused_conn.executed) < len(plain_conn.executed)
    shared = [o for o in fused.outcomes if o.evidence is not None and o.evidence.fused_with]
    assert shared, "some claims must have been fused"
    for o in shared:
        assert o.evidence is not None
        assert o.semantic_hash not in o.evidence.fused_with
        assert "m0__" in (o.evidence.sql or "")


def test_duplicate_claims_run_once(conn: DBAPIConnector) -> None:
    logged = Logged(conn)
    report = Checker(logged, fuse=False, catalog=NullCatalog()).check(
        "ROWS orders VALUE == 100\nROWS orders VALUE == 100"
    )
    assert [o.verdict for o in report.outcomes] == [Verdict.PASS, Verdict.PASS]
    assert len(logged.executed) == 1


def test_composite_members_with_identical_columns_fuse(conn: DBAPIConnector) -> None:
    cols = (Ident("order_id"), Ident("line"))
    plans: list[ProbePlan] = [
        CompositeKeyAggregate(ITEMS, cols, (Metric.ROW_COUNT,)),
        CompositeKeyAggregate(ITEMS, cols, (Metric.NON_NULL,)),
    ]
    probes = optimize([WorkItem(p) for p in plans])
    assert len(probes) == 1 and isinstance(probes[0].plan, FusedAggregate)
    fused = Checker(conn).measure(plans)
    plain = Checker(conn, fuse=False).measure(plans)
    assert [m.payload for m in fused] == [m.payload for m in plain]
    assert fused[0].payload == {"row_count": 200}
    assert fused[1].payload == {"non_null": 200}
    assert fused[0].evidence is not None and fused[0].evidence.fused_with


def test_fl405_plan_stays_unfused_and_errors(conn: DBAPIConnector) -> None:
    block = SampleSpec("bernoulli", Decimal("0.5"), 1)
    bad = ColumnAggregate(ORDERS, Ident("id"), (Metric.MAX_PER_KEY,), sample=block)
    good = ColumnAggregate(ORDERS, Ident("id"), (Metric.ROW_COUNT,), sample=block)
    assert len(optimize([WorkItem(bad), WorkItem(good)])) == 2
    m_bad, m_good = Checker(conn).measure([bad, good])
    assert m_bad.error is not None and m_bad.error.code == "FL405"
    assert m_bad.payload is None
    assert m_good.error is None and m_good.payload is not None


# -- R1 measure ------------------------------------------------------------------------------


def _claim(text: str) -> CheckedClaim:
    parsed = parse(text)
    bag = DiagnosticBag()
    (claim,) = analyze(
        parsed.document, standard_pack().verb_registry(), catalog=NullCatalog(), bag=bag
    )
    return claim


def _recording_pack(seen: dict[str, Mapping[str, object]]) -> Any:
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


def _norm(payload: Mapping[str, object] | None) -> object:
    if payload is None:
        return None
    out = {k: v for k, v in payload.items() if k != "server_now"}
    if "rows" in out:
        rows = out["rows"]
        assert isinstance(rows, list)
        out["rows"] = sorted((tuple(sorted(r.items(), key=str)) for r in rows), key=str)
    return out


def test_measure_payload_equals_what_derive_receives(conn: DBAPIConnector) -> None:
    pack = standard_pack()
    cases = [c for c in (*CASES, *COMPOSITE_JOIN, COVERAGE_FAIL) if c.verb in _PLANNED]
    texts = [c.claim for c in cases] + ["UNIQUE order_items.order_id WITH [line]"]
    seen: dict[str, Mapping[str, object]] = {}
    Checker(conn, pack=_recording_pack(seen)).check("\n".join(texts))
    claims = [_claim(t) for t in texts]
    plans = []
    for claim in claims:
        contract = pack.get(claim.verb)
        assert contract is not None
        plans.append(plan_for(contract, claim))
    measured = Checker(conn).measure(plans)
    kinds = set()
    for claim, plan, m in zip(claims, plans, measured, strict=True):
        assert isinstance(m, Measurement) and m.error is None
        assert m.plan == plan
        assert _norm(m.payload) == _norm(seen.get(claim.semantic_hash)), claim.canonical_text
        assert m.evidence is not None and m.evidence.method == "exact"
        if m.payload is not None:
            assert claim.semantic_hash in seen, claim.canonical_text
        kinds.add(type(plan))
    assert kinds == {
        ColumnAggregate,
        CompositeKeyAggregate,
        ValueMembership,
        KeyOverlap,
        PatternCoverage,
    }


_PLANNED = {
    "ROWS", "NULLS", "DEAD", "UNIQUE", "FANOUT", "JOIN", "VALUES", "RANGE", "PATTERN",
    "FRESH", "COUNT", "DISTINCT", "SUM", "AVG", "MIN", "MAX",
}  # fmt: skip


def test_measure_dedupes_and_keeps_input_order(conn: DBAPIConnector) -> None:
    a = ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,))
    b = ColumnAggregate(CUSTOMERS, Ident("id"), (Metric.NON_NULL,))
    logged = Logged(conn)
    out = Checker(logged).measure([a, b, a])
    assert [m.payload for m in out] == [{"row_count": 100}, {"non_null": 10}, {"row_count": 100}]
    assert len(logged.executed) == 2


def test_measure_failure_sets_error_and_never_raises(conn: DBAPIConnector) -> None:
    missing = ColumnAggregate(_t("no_such_table"), None, (Metric.ROW_COUNT,))
    ok = ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,))
    bad, good = Checker(conn).measure([missing, ok])
    assert bad.payload is None and bad.error is not None and bad.error.code == "FL700"
    assert bad.evidence is not None and bad.evidence.sql
    assert good.error is None and good.payload == {"row_count": 100}


def test_measure_empty_result_payload(conn: DBAPIConnector) -> None:
    (m,) = Checker(conn).measure([ColumnAggregate(_t("empty_t"), Ident("v"), (Metric.SUM,))])
    assert m.error is None and m.payload == {"sum_value": None}


def test_value_membership_top_k_matches_group_by(duck: Any, conn: DBAPIConnector) -> None:
    plan = ValueMembership(ORDERS, Ident("status"), declared=(), report_limit=3)
    (m,) = Checker(conn).measure([plan])
    assert m.error is None and m.payload is not None
    rows = m.payload["rows"]
    assert isinstance(rows, list)
    got = [(r["value"], r["freq"]) for r in rows if r["kind"] == "undeclared"]
    got.sort(key=lambda vf: (-int(vf[1]), str(vf[0])))
    want = duck.execute(
        "SELECT CAST(status AS VARCHAR) AS v, COUNT(*) AS n FROM orders "
        "WHERE status IS NOT NULL GROUP BY 1 ORDER BY n DESC, v ASC LIMIT 3"
    ).fetchall()
    assert [(v, int(n)) for v, n in got] == [(v, int(n)) for v, n in want]


def test_sampled_measurement_carries_sample_and_sampled_rows(conn: DBAPIConnector) -> None:
    whole = SampleSpec("keyhash", Decimal(1), 7, (Ident("id"),))
    half = SampleSpec("keyhash", Decimal("0.5"), 7, (Ident("id"),))
    exact = ColumnAggregate(ORDERS, Ident("id"), (Metric.ROW_COUNT, Metric.NON_NULL))
    full = dataclasses.replace(exact, sample=whole)
    part = dataclasses.replace(exact, sample=half)
    m_exact, m_full, m_part, m_again = Checker(conn).measure([exact, full, part, part])
    assert m_exact.evidence is not None and m_exact.evidence.method == "exact"
    assert m_exact.evidence.sample is None
    assert m_full.payload == {"row_count": 100, "non_null": 100, "sampled_rows": 100}
    assert m_full.evidence is not None and m_full.evidence.sample == whole
    assert m_part.evidence is not None and m_part.evidence.method == "sampled:keyhash:0.5:7"
    assert (
        m_part.payload is not None and m_part.payload["sampled_rows"] == m_part.payload["row_count"]
    )
    assert 0 < int(m_part.payload["row_count"]) < 100  # type: ignore[call-overload]
    assert m_again.payload == m_part.payload


def test_sampled_key_overlap_has_per_side_counts(conn: DBAPIConnector) -> None:
    s = SampleSpec("keyhash", Decimal(1), 3, (Ident("customer_id"),))
    r = SampleSpec("keyhash", Decimal(1), 3, (Ident("id"),))
    plan = KeyOverlap(
        (ColumnRef(ORDERS, Ident("customer_id")),),
        (ColumnRef(CUSTOMERS, Ident("id")),),
        sample=s,
        right_sample=r,
    )
    (m,) = Checker(conn).measure([plan])
    assert m.error is None and m.payload is not None
    assert m.payload["sampled_left_rows"] == 100 and m.payload["sampled_right_rows"] == 10
    assert m.evidence is not None and m.evidence.method == "sampled:keyhash:1:3"


def test_check_rejects_sampled_plans_fl404(conn: DBAPIConnector) -> None:
    pack = standard_pack().extend("sampling", "1.0")
    rows = pack.get("ROWS")
    assert rows is not None
    sample = SampleSpec("system", Decimal("0.5"))

    def sampled_plan(claim: CheckedClaim) -> ProbePlan:
        return ColumnAggregate(claim.table, None, (Metric.ROW_COUNT,), sample=sample)

    pack.override(dataclasses.replace(rows, plan=sampled_plan))
    logged = Logged(conn)
    (outcome,) = (
        Checker(logged, pack=pack, catalog=NullCatalog()).check("ROWS orders VALUE >= 1").outcomes
    )
    assert outcome.verdict is Verdict.ERROR and outcome.explanation.startswith("FL404")
    assert [d["code"] for d in outcome.diagnostics if d["severity"] == "error"] == ["FL404"]
    assert logged.executed == []


# -- R5 estimate -----------------------------------------------------------------------------


def test_estimate_never_executes(conn: DBAPIConnector) -> None:
    logged = Logged(conn, dry_run=4096)
    sampled = ColumnAggregate(
        ORDERS, None, (Metric.ROW_COUNT,), sample=SampleSpec("bernoulli", Decimal("0.1"))
    )
    by_text = Checker(logged).estimate(
        "JOIN orders.customer_id -> customers.id MATCH >= 99%\nROWS orders VALUE >= 1"
    )
    by_plan = Checker(logged).estimate([sampled])
    assert logged.executed == []
    assert [e.bytes for e in by_text + by_plan] == [4096, 4096, 4096]
    assert {e.method for e in by_text + by_plan} == {"dry_run"}
    assert by_plan[0].key == canonical_key(sampled)
    assert "TABLESAMPLE" in (by_plan[0].sql or "")
    assert (
        by_text[0].key
        == _claim("JOIN orders.customer_id -> customers.id MATCH >= 99%").semantic_hash
    )
    assert len(logged.dry_runs) == 3


def test_estimate_without_dry_run_is_unknown(conn: DBAPIConnector) -> None:
    logged = Logged(conn)
    (est,) = Checker(logged).estimate("ROWS orders VALUE >= 1")
    assert est.bytes is None and est.method == "unknown" and est.sql
    assert logged.executed == []


def test_cost_guard_refuses_before_executing(conn: DBAPIConnector) -> None:
    logged = Logged(conn, dry_run=10**9)
    (outcome,) = (
        Checker(logged, max_bytes=1000, catalog=NullCatalog())
        .check("ROWS orders VALUE >= 1")
        .outcomes
    )
    assert outcome.verdict is Verdict.ERROR and outcome.explanation.startswith("FL701")
    assert logged.executed == []
    logged.dry_run_value = 10
    (ok,) = Checker(logged, max_bytes=1000).check("ROWS orders VALUE >= 1").outcomes
    assert ok.verdict is Verdict.PASS


# -- errors, async ---------------------------------------------------------------------------


def test_warehouse_error_is_error_not_no_data(conn: DBAPIConnector) -> None:
    report = Checker(conn, catalog=NullCatalog()).check("ROWS no_such_table VALUE >= 1")
    (outcome,) = report.outcomes
    assert outcome.verdict is Verdict.ERROR and outcome.explanation.startswith("FL700")
    assert outcome.evidence is not None and outcome.evidence.sql
    assert "FL700" in report.diagnostics.codes()


def test_timeout_is_error_fl702(conn: DBAPIConnector) -> None:
    class Slow(Logged):
        def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
            raise ExecutionError("query timed out after 1s", code="FL702")

    (outcome,) = Checker(Slow(conn), timeout_s=1).check("ROWS orders VALUE >= 1").outcomes
    assert outcome.verdict is Verdict.ERROR and outcome.explanation.startswith("FL702")


async def _acheck(checker: Checker, text: str) -> Any:
    return await checker.acheck(text)


def test_acheck_matches_check(conn: DBAPIConnector) -> None:
    import asyncio

    cases = [c for c in CASES if c.catalog == "warehouse" and c.verb != "FRESH"]
    text = "\n".join(c.claim for c in cases)
    sync = Checker(conn).check(text)
    async_ = asyncio.run(_acheck(Checker(conn), text))
    assert [_comparable(o) for o in async_.outcomes] == [_comparable(o) for o in sync.outcomes]


def test_ameasure_matches_measure(conn: DBAPIConnector) -> None:
    import asyncio

    plans = [
        ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,)),
        ColumnAggregate(ORDERS, Ident("amount"), (Metric.SUM,)),
        ColumnAggregate(_t("no_such_table"), None, (Metric.ROW_COUNT,)),
    ]
    sync = Checker(conn).measure(plans)
    async_ = asyncio.run(Checker(conn).ameasure(plans))
    assert [m.payload for m in async_] == [m.payload for m in sync]
    assert async_[2].error is not None and async_[2].error.code == "FL700"
