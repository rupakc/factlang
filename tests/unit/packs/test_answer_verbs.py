"""COUNT DISTINCT SUM AVG MIN MAX: ColumnAggregate -> ``value``, Within VALUE (spec 6.4)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from packs_helpers import Verdict, assert_pack_complete, build_claim, judge

from factlang.contract import Contract, ContractPack, plan_for
from factlang.diagnostics import DiagnosticBag
from factlang.ir import ColumnAggregate, Metric
from factlang.judge import decide_facts
from factlang.packs.standard import avg as avg_verb
from factlang.packs.standard import count as count_verb
from factlang.packs.standard import distinct as distinct_verb
from factlang.packs.standard import max as max_verb
from factlang.packs.standard import min as min_verb
from factlang.packs.standard import standard_pack
from factlang.packs.standard import sum as sum_verb
from factlang.semantics.analyzer import analyze
from factlang.syntax import parse

# verb -> (contract, source stem, metric, probed column, payload key)
VERBS: dict[str, tuple[Contract, str, Metric, str | None, str]] = {
    "COUNT": (count_verb.CONTRACT, "COUNT orders", Metric.ROW_COUNT, None, "row_count"),
    "DISTINCT": (
        distinct_verb.CONTRACT,
        "DISTINCT orders.customer_id",
        Metric.DISTINCT_KEYS,
        "customer_id",
        "distinct_keys",
    ),
    "SUM": (sum_verb.CONTRACT, "SUM order_items.price", Metric.SUM, "price", "sum_value"),
    "AVG": (avg_verb.CONTRACT, "AVG order_items.price", Metric.AVG, "price", "avg_value"),
    "MIN": (min_verb.CONTRACT, "MIN order_items.price", Metric.MIN_VALUE, "price", "min_value"),
    "MAX": (max_verb.CONTRACT, "MAX order_items.price", Metric.MAX_VALUE, "price", "max_value"),
}
ids = list(VERBS)


@pytest.mark.parametrize("verb", ids)
def test_contract_is_complete(verb: str) -> None:
    pack = ContractPack("t", "1")
    pack.register(VERBS[verb][0])
    assert_pack_complete(pack)


@pytest.mark.parametrize("verb", ids)
def test_plan(verb: str) -> None:
    contract, stem, metric, column, _ = VERBS[verb]
    c = build_claim(f"{stem} VALUE >= 1 WHERE status = 'paid'")
    p = plan_for(contract, c)
    assert isinstance(p, ColumnAggregate)
    assert p.table == c.table and p.metrics == (metric,) and p.filter == c.filter
    assert c.filter is not None
    assert (p.column.text if p.column is not None else None) == column


@pytest.mark.parametrize("verb", ids)
def test_spec_shape(verb: str) -> None:
    spec = VERBS[verb][0].spec
    attrs = {a.name: a for a in spec.attrs}
    assert set(attrs) == {"VALUE", "TOLERANCE", "ABS_TOLERANCE"}
    assert attrs["VALUE"].required and attrs["VALUE"].ops == ("==", "!=", "<", "<=", ">", ">=")
    assert (attrs["TOLERANCE"].type, attrs["TOLERANCE"].ops) == ("RATIO", ())
    assert (attrs["ABS_TOLERANCE"].type, attrs["ABS_TOLERANCE"].ops) == ("NUMBER", ())
    assert spec.exclusive == (("TOLERANCE", "ABS_TOLERANCE"),) and spec.filterable
    assert attrs["VALUE"].type == ("NUMBER_OR_TEMPORAL" if verb in ("MIN", "MAX") else "NUMBER")


@pytest.mark.parametrize("verb", ids)
@pytest.mark.parametrize(
    ("clause", "measured", "verdict"),
    [
        ("VALUE == 10", 10, Verdict.PASS),
        ("VALUE == 10", Decimal("10.00"), Verdict.PASS),
        ("VALUE == 10", 11, Verdict.FAIL),
        ("VALUE != 10", 11, Verdict.PASS),
        ("VALUE >= 10", 9, Verdict.FAIL),
        ("VALUE < 10", 9, Verdict.PASS),
        ("VALUE == 100 TOLERANCE 1%", 100.5, Verdict.PASS),
        ("VALUE == 100 TOLERANCE 1%", 102, Verdict.FAIL),
        ("VALUE == 100 ABS_TOLERANCE 2", 102, Verdict.PASS),
        ("VALUE == 100 ABS_TOLERANCE 2", 103, Verdict.FAIL),
        ("VALUE >= 100 TOLERANCE 10%", 91, Verdict.PASS),
    ],
)
def test_decide(verb: str, clause: str, measured: object, verdict: Verdict) -> None:
    contract, stem, _, _, key = VERBS[verb]
    v, _, facts = judge(contract, f"{stem} {clause}", {key: measured})
    assert v is verdict
    assert facts == {"value": measured if isinstance(measured, float) else Decimal(str(measured))}


@pytest.mark.parametrize("verb", ids)
def test_decimal_exact_compare(verb: str) -> None:
    """No float round-trip: 0.1 + 0.2 style values compare exactly as Decimals."""
    contract, stem, _, _, key = VERBS[verb]
    assert judge(contract, f"{stem} VALUE == 0.3", {key: Decimal("0.3")})[0] is Verdict.PASS
    assert judge(contract, f"{stem} VALUE == 0.3", {key: Decimal("0.30000000001")})[0] is (
        Verdict.FAIL
    )
    big = "12345678901234567890.12345678901234567890"
    assert judge(contract, f"{stem} VALUE == {big}", {key: Decimal(big)})[0] is Verdict.PASS
    last = "12345678901234567890.12345678901234567891"
    assert judge(contract, f"{stem} VALUE == {big}", {key: Decimal(last)})[0] is Verdict.FAIL
    assert judge(contract, f"{stem} VALUE == 7", {key: 7})[2]["value"] == Decimal(7)


@pytest.mark.parametrize("verb", ["COUNT", "DISTINCT"])
def test_zero_is_a_measurement(verb: str) -> None:
    contract, stem, _, _, key = VERBS[verb]
    assert judge(contract, f"{stem} VALUE == 0", {key: 0})[0] is Verdict.PASS
    assert judge(contract, f"{stem} VALUE >= 1", {key: 0})[0] is Verdict.FAIL


@pytest.mark.parametrize("verb", ["SUM", "AVG", "MIN", "MAX"])
@pytest.mark.parametrize("clause", ["VALUE == 0", "VALUE >= 0", "VALUE != 0", "VALUE <= 0"])
def test_empty_or_all_null_is_no_data_never_zero(verb: str, clause: str) -> None:
    contract, stem, _, _, key = VERBS[verb]
    v, why, facts = judge(contract, f"{stem} {clause}", {key: None})
    assert v is Verdict.NO_DATA and facts == {"value": None}
    assert "not measured" in why


@pytest.mark.parametrize("verb", ["COUNT", "DISTINCT"])
def test_unmeasured_count_is_no_data(verb: str) -> None:
    contract, stem, _, _, key = VERBS[verb]
    assert judge(contract, f"{stem} VALUE == 0", {key: None})[0] is Verdict.NO_DATA


def test_explanation_names_the_numbers() -> None:
    _, why, _ = judge(count_verb.CONTRACT, "COUNT orders VALUE >= 1000", {"row_count": 999})
    assert why == "value 999 < 1000"


def test_tolerance_is_relative_to_the_target() -> None:
    src = "SUM order_items.price VALUE == 1000 TOLERANCE 0.5%"
    assert judge(sum_verb.CONTRACT, src, {"sum_value": 1005})[0] is Verdict.PASS
    assert judge(sum_verb.CONTRACT, src, {"sum_value": 1005.01})[0] is Verdict.FAIL


# --- MIN / MAX temporal ------------------------------------------------------------------

D = dt.date
T = dt.datetime


@pytest.mark.parametrize("verb", ["MIN", "MAX"])
@pytest.mark.parametrize(
    ("clause", "measured", "verdict"),
    [
        ("VALUE >= DATE '2025-01-31'", D(2025, 2, 1), Verdict.PASS),
        ("VALUE >= DATE '2025-01-31'", D(2025, 1, 30), Verdict.FAIL),
        ("VALUE == DATE '2025-01-31'", D(2025, 1, 31), Verdict.PASS),
        (
            "VALUE <= TIMESTAMP '2025-01-31T12:00:00Z'",
            T(2025, 1, 31, 12),
            Verdict.PASS,
        ),  # naive=UTC
        (
            "VALUE <= TIMESTAMP '2025-01-31T12:00:00Z'",
            T(2025, 1, 31, 13, tzinfo=dt.timezone(dt.timedelta(hours=2))),
            Verdict.PASS,  # 11:00Z
        ),
        ("VALUE < TIMESTAMP '2025-01-31T12:00:00Z'", T(2025, 1, 31, 12), Verdict.FAIL),
        ("VALUE >= TIMESTAMP '2025-01-31T00:00:00Z'", D(2025, 1, 31), Verdict.PASS),  # midnight
    ],
)
def test_temporal(verb: str, clause: str, measured: object, verdict: Verdict) -> None:
    contract, _, _, _, key = VERBS[verb]
    stem = f"{verb} orders.created_at"
    assert judge(contract, f"{stem} {clause}", {key: measured})[0] is verdict


@pytest.mark.parametrize("verb", ["MIN", "MAX"])
def test_temporal_kind_mismatch_is_an_error(verb: str) -> None:
    contract, _, _, _, key = VERBS[verb]
    stem = f"{verb} orders.created_at"
    # a number against a temporal target, and a date against a number target
    v, why, _ = judge(contract, f"{stem} VALUE >= DATE '2025-01-31'", {key: 5})
    assert v is Verdict.ERROR and "cannot compare" in why
    v, _, _ = judge(contract, f"{stem} VALUE >= 5", {key: D(2025, 1, 31)})
    assert v is Verdict.ERROR


@pytest.mark.parametrize("verb", ["MIN", "MAX"])
def test_temporal_with_a_tolerance_is_an_error(verb: str) -> None:
    contract, _, _, _, key = VERBS[verb]
    src = f"{verb} orders.created_at VALUE >= DATE '2025-01-31' ABS_TOLERANCE 1"
    assert judge(contract, src, {key: D(2025, 2, 1)})[0] is Verdict.ERROR


@pytest.mark.parametrize("verb", ["MIN", "MAX"])
def test_temporal_empty_is_no_data(verb: str) -> None:
    contract, _, _, _, key = VERBS[verb]
    src = f"{verb} orders.created_at VALUE >= DATE '2025-01-31'"
    assert judge(contract, src, {key: None})[0] is Verdict.NO_DATA


def test_sum_and_avg_reject_temporal_literals() -> None:
    for verb in ("SUM", "AVG", "COUNT", "DISTINCT"):
        parsed = parse(f"{verb} orders.created_at VALUE >= DATE '2025-01-31'")
        bag = DiagnosticBag()
        analyze(parsed.document, standard_pack().verb_registry(), bag=bag)
        assert "FL305" in bag.codes(), verb


def test_decide_facts_signature_is_used_directly() -> None:
    c = build_claim("MAX order_items.price VALUE == 5")
    v, _, _ = decide_facts(max_verb.CONTRACT.derive, max_verb.CONTRACT.decide, c, {"max_value": 5})
    assert v is Verdict.PASS


# --- float noise (spec 6.4): a float result stays a float, no-band ==/!= use isclose ----------


def test_float_results_stay_float_facts() -> None:
    v, _, facts = judge(
        avg_verb.CONTRACT, "AVG order_items.price VALUE == 0.15", {"avg_value": (0.1 + 0.2) / 2}
    )
    assert v is Verdict.PASS and isinstance(facts["value"], float)
    assert (
        judge(sum_verb.CONTRACT, "SUM order_items.price VALUE == 0.3", {"sum_value": 0.1 + 0.2})[0]
        is Verdict.PASS
    )
    assert (
        judge(sum_verb.CONTRACT, "SUM order_items.price VALUE != 0.3", {"sum_value": 0.1 + 0.2})[0]
        is Verdict.FAIL
    )
    assert (
        judge(min_verb.CONTRACT, "MIN order_items.price VALUE == 0.3", {"min_value": 0.1 + 0.2})[0]
        is Verdict.PASS
    )


def test_decimal_results_stay_exact() -> None:
    noisy = Decimal("0.30000000000000004")
    assert (
        judge(sum_verb.CONTRACT, "SUM order_items.price VALUE == 0.3", {"sum_value": noisy})[0]
        is Verdict.FAIL
    )
    assert (
        judge(sum_verb.CONTRACT, "SUM order_items.price VALUE == 0.3", {"sum_value": 0.1 + 0.2})[0]
        is Verdict.PASS
    )


def test_numeric_strings_from_drivers() -> None:
    v, _, facts = judge(
        sum_verb.CONTRACT, "SUM order_items.price VALUE == 12.50", {"sum_value": "12.50"}
    )
    assert v is Verdict.PASS and facts["value"] == Decimal("12.50")
    assert (
        judge(count_verb.CONTRACT, "COUNT orders VALUE == 5", {"row_count": "5"})[0] is Verdict.PASS
    )
    assert (
        judge(sum_verb.CONTRACT, "SUM order_items.price VALUE == 1", {"sum_value": "NaN"})[0]
        is Verdict.NO_DATA
    )
    assert (
        judge(sum_verb.CONTRACT, "SUM order_items.price VALUE == 1", {"sum_value": "n/a"})[0]
        is Verdict.ERROR
    )


def test_counts_are_ints() -> None:
    assert (
        judge(count_verb.CONTRACT, "COUNT orders VALUE == 5", {"row_count": Decimal("5")})[2][
            "value"
        ]
        == 5
    )
    f = judge(
        distinct_verb.CONTRACT, "DISTINCT orders.customer_id VALUE == 5", {"distinct_keys": 5.0}
    )[2]
    assert type(f["value"]) is int


# --- DATE target over a TIMESTAMP result: the result's UTC calendar date ------------------------

_EDGE = T(2025, 1, 31, 23, 59, 59)
_NEXT = T(2025, 2, 1, 0, 0, 0)
_PLUS2 = dt.timezone(dt.timedelta(hours=2))


@pytest.mark.parametrize("verb", ["MIN", "MAX"])
@pytest.mark.parametrize(
    ("op", "measured", "verdict"),
    [
        ("==", _EDGE, Verdict.PASS),
        ("==", _NEXT, Verdict.FAIL),
        ("!=", _NEXT, Verdict.PASS),
        ("!=", _EDGE, Verdict.FAIL),
        ("<", _EDGE, Verdict.FAIL),  # still the 31st
        ("<", T(2025, 1, 30, 23, 59), Verdict.PASS),
        ("<=", _EDGE, Verdict.PASS),
        ("<=", _NEXT, Verdict.FAIL),
        (">", _EDGE, Verdict.FAIL),
        (">", _NEXT, Verdict.PASS),
        (">=", T(2025, 1, 31, 0, 0), Verdict.PASS),
        (">=", T(2025, 1, 30, 23, 59, 59), Verdict.FAIL),
        ("==", T(2025, 2, 1, 1, tzinfo=_PLUS2), Verdict.PASS),  # 2025-01-31T23:00Z
    ],
)
def test_date_target_over_timestamp_result(
    verb: str, op: str, measured: object, verdict: Verdict
) -> None:
    contract, _, _, _, key = VERBS[verb]
    src = f"{verb} events.at VALUE {op} DATE '2025-01-31'"
    assert judge(contract, src, {key: measured})[0] is verdict
