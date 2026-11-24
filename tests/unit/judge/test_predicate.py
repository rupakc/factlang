from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from factlang.diagnostics import Span
from factlang.judge import (
    Always,
    And,
    Compare,
    CompareUsingClaimOp,
    Not,
    Or,
    Predicate,
    WhenClaimHas,
    Within,
)
from factlang.semantics.checked import CheckedAttr, CheckedClaim
from factlang.semantics.refs import Ident, TableRef


def claim(**attrs: tuple[str | None, object]) -> CheckedClaim:
    return CheckedClaim(
        verb="COUNT",
        operands=(TableRef(None, None, Ident("orders")),),
        attrs={k: CheckedAttr(op, v, "NUMBER") for k, (op, v) in attrs.items()},
        filter=None,
        canonical_text="COUNT orders",
        semantic_hash="sha256:x",
        span=Span(1, 1, 2),
        source_text="COUNT orders",
        comment=None,
    )


NOCLAIM = claim()
TRUE = Compare("t", "==", value=1)
FALSE = Compare("f", "==", value=1)
NONE = Compare("n", "==", value=1)
FACTS: dict[str, object] = {"t": 1, "f": 0, "n": None}


def ev(p: Predicate, facts: dict[str, object] | None = None, c: CheckedClaim = NOCLAIM) -> object:
    return p.evaluate(FACTS if facts is None else facts, c)[0]


# --- Compare ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("op", "a", "b", "want"),
    [
        ("==", 1, 1, True),
        ("==", 1, 2, False),
        ("!=", 1, 2, True),
        ("<", 1, 2, True),
        ("<", 2, 2, False),
        ("<=", 2, 2, True),
        (">", 3, 2, True),
        (">=", 1, 2, False),
        ("=", Decimal("1.0"), 1, True),
    ],
)
def test_compare_ops(op: str, a: object, b: object, want: bool) -> None:
    assert ev(Compare("x", op, value=b), {"x": a}) is want


def test_compare_missing_or_none_is_undecided() -> None:
    assert Compare("x", "<=", value=1).evaluate({}, NOCLAIM) == (None, "x was not measured")
    assert Compare("x", "<=", value=1).evaluate({"x": None}, NOCLAIM) == (
        None,
        "x was not measured",
    )


def test_none_is_never_coerced_to_zero() -> None:
    assert ev(Compare("max_rows_per_key", "<=", value=1), {"max_rows_per_key": None}) is None


def test_compare_against_attr() -> None:
    c = claim(VALUE=(">=", Decimal(1000)))
    p = Compare("row_count", ">=", attr="VALUE")
    assert p.evaluate({"row_count": 1500}, c)[0] is True
    assert p.evaluate({"row_count": 999}, c)[0] is False
    assert p.reads() == ({"row_count"}, {"VALUE"})


def test_compare_needs_exactly_one_target_and_a_known_op() -> None:
    with pytest.raises(ValueError):
        Compare("x", "==")
    with pytest.raises(ValueError):
        Compare("x", "==", value=1, attr="VALUE")
    with pytest.raises(ValueError):
        Compare("x", "~", value=1)


def test_explanation_names_the_numbers() -> None:
    ok, why = Compare("match_rate", ">=", value=Decimal("0.99")).evaluate(
        {"match_rate": Decimal("0.9774")}, NOCLAIM
    )
    assert ok is False and why == "match_rate 0.9774 < 0.99"
    ok, why = Compare("match_rate", ">=", value=Decimal("0.99")).evaluate(
        {"match_rate": Decimal("1")}, NOCLAIM
    )
    assert ok is True and why == "match_rate 1 >= 0.99"


def test_decimal_exact_compare() -> None:
    # == on a float side uses math.isclose
    assert ev(Compare("x", "==", value=Decimal("0.3")), {"x": 0.1 + 0.2}) is True
    assert ev(Compare("x", "!=", value=Decimal("0.3")), {"x": 0.1 + 0.2}) is False
    # ... while two Decimals compare exactly, at any magnitude
    big = Decimal("12345678901234567890")
    assert ev(Compare("x", "==", value=Decimal("12345678901234567891")), {"x": big}) is False
    assert ev(Compare("x", "<", value=Decimal("12345678901234567891")), {"x": big}) is True
    # big ints compare exactly too (no float round-trip)
    assert ev(Compare("x", "==", value=12345678901234567891), {"x": 12345678901234567890}) is False
    assert ev(Compare("x", "==", value=Decimal("0.1")), {"x": Decimal("0.10")}) is True


def test_float_ordering_agrees_with_float_equality() -> None:
    x = {"x": 0.1 + 0.2}
    assert ev(Compare("x", "<=", value=Decimal("0.3")), x) is True
    assert ev(Compare("x", ">", value=Decimal("0.3")), x) is False
    assert ev(Compare("x", "<", value=Decimal("0.3")), x) is False


def test_non_numeric_compare() -> None:
    import datetime as dt

    d = dt.date(2026, 1, 1)
    assert ev(Compare("x", "==", value="a"), {"x": "a"}) is True
    assert ev(Compare("x", "<", value=dt.date(2026, 1, 2)), {"x": d}) is True
    assert ev(Compare("x", "==", value=True), {"x": True}) is True
    with pytest.raises(TypeError):
        Compare("x", "<", value=1).evaluate({"x": "a"}, NOCLAIM)


_UTC = dt.timezone.utc
_INCOMPATIBLE = [
    ("a", 1),
    (1, "a"),
    (True, 1),
    (Decimal(1), True),
    ("2026-01-01", dt.date(2026, 1, 1)),
    (dt.date(2026, 1, 1), dt.datetime(2026, 1, 1)),
    (dt.datetime(2026, 1, 1), dt.datetime(2026, 1, 1, tzinfo=_UTC)),
    ("true", True),
    (float("nan"), 1),
]


@pytest.mark.parametrize(("a", "b"), _INCOMPATIBLE)
@pytest.mark.parametrize("op", ["==", "!=", "<", "<=", ">", ">="])
def test_incompatible_kinds_raise_for_every_op(a: object, b: object, op: str) -> None:
    with pytest.raises(TypeError):
        Compare("x", op, value=b).evaluate({"x": a}, NOCLAIM)


def test_same_kind_temporals_compare() -> None:
    aware = dt.datetime(2026, 1, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=1)))
    assert ev(Compare("x", "==", value=dt.datetime(2026, 1, 1, tzinfo=_UTC)), {"x": aware}) is True
    assert ev(Compare("x", "<", value=dt.datetime(2026, 1, 2)), {"x": dt.datetime(2026, 1, 1)})


# --- CompareUsingClaimOp --------------------------------------------------------------


def test_compare_using_claim_op() -> None:
    p = CompareUsingClaimOp("row_count", "VALUE")
    assert p.evaluate({"row_count": 5}, claim(VALUE=("<", Decimal(10))))[0] is True
    assert p.evaluate({"row_count": 5}, claim(VALUE=(">", Decimal(10))))[0] is False
    assert p.evaluate({"row_count": 10}, claim(VALUE=("=", Decimal(10))))[0] is True
    assert p.evaluate({"row_count": None}, claim(VALUE=("<", Decimal(10))))[0] is None
    assert p.reads() == ({"row_count"}, {"VALUE"})


# --- Within (answer verbs, spec 6.4) --------------------------------------------------


def test_within_exact_without_tolerance() -> None:
    p = Within("value", "VALUE")
    c = claim(VALUE=("==", Decimal(1717)))
    assert p.evaluate({"value": 1717}, c)[0] is True
    assert p.evaluate({"value": 1718}, c)[0] is False
    assert p.evaluate({"value": 1717.0000000000002}, c)[0] is True  # isclose on a float
    assert p.evaluate({"value": None}, c) == (None, "value was not measured")
    assert p.reads() == ({"value"}, {"VALUE", "TOLERANCE", "ABS_TOLERANCE"})


def test_within_bare_op_is_equality() -> None:
    p = Within("value", "VALUE")
    assert p.evaluate({"value": 5}, claim(VALUE=(None, Decimal(5))))[0] is True
    assert p.evaluate({"value": 6}, claim(VALUE=(None, Decimal(5))))[0] is False


# (op, actual, want) for target 100; without tolerance, then with a band of 5
_EXACT = [
    ("==", 100, True),
    ("==", 101, False),
    ("!=", 100, False),
    ("!=", 101, True),
    (">=", 100, True),
    (">=", 99, False),
    (">", 100, False),
    (">", 101, True),
    ("<=", 100, True),
    ("<=", 101, False),
    ("<", 100, False),
    ("<", 99, True),
]
_BAND5 = [
    ("==", 105, True),
    ("==", 95, True),
    ("==", Decimal("105.01"), False),
    ("!=", 105, False),
    ("!=", Decimal("94.99"), True),
    (">=", 95, True),
    (">=", Decimal("94.99"), False),
    (">", 95, False),
    (">", Decimal("95.01"), True),
    ("<=", 105, True),
    ("<=", Decimal("105.01"), False),
    ("<", 105, False),
    ("<", Decimal("104.99"), True),
]


@pytest.mark.parametrize(("op", "actual", "want"), _EXACT)
def test_within_honours_claim_op_exact(op: str, actual: object, want: bool) -> None:
    c = claim(VALUE=(op, Decimal(100)))
    assert Within("value", "VALUE").evaluate({"value": actual}, c)[0] is want


@pytest.mark.parametrize(("op", "actual", "want"), _BAND5)
def test_within_honours_claim_op_relative(op: str, actual: object, want: bool) -> None:
    c = claim(VALUE=(op, Decimal(100)), TOLERANCE=(None, Decimal("0.05")))
    assert Within("value", "VALUE").evaluate({"value": actual}, c)[0] is want


@pytest.mark.parametrize(("op", "actual", "want"), _BAND5)
def test_within_honours_claim_op_absolute(op: str, actual: object, want: bool) -> None:
    c = claim(VALUE=(op, Decimal(100)), ABS_TOLERANCE=(None, Decimal(5)))
    assert Within("value", "VALUE").evaluate({"value": actual}, c)[0] is want


def test_within_not_equal_on_equal_value_fails() -> None:
    ok, why = Within("value", "VALUE").evaluate({"value": 5}, claim(VALUE=("!=", Decimal(5))))
    assert ok is False and "value 5" in why and "5" in why


def test_within_band_is_the_larger_of_relative_and_absolute() -> None:
    both = claim(
        VALUE=("==", Decimal(100)),
        TOLERANCE=(None, Decimal("0.01")),
        ABS_TOLERANCE=(None, Decimal(3)),
    )
    p = Within("value", "VALUE")
    assert p.evaluate({"value": 103}, both)[0] is True  # abs 3 > rel 1
    assert p.evaluate({"value": 104}, both)[0] is False


def test_within_float_fact_with_tolerance() -> None:
    c = claim(VALUE=("==", Decimal("0.3")), ABS_TOLERANCE=(None, Decimal("0.01")))
    p = Within("value", "VALUE")
    assert p.evaluate({"value": 0.1 + 0.2}, c)[0] is True
    assert p.evaluate({"value": 0.311}, c)[0] is False
    r = claim(VALUE=("<=", Decimal(1000)), TOLERANCE=(None, Decimal("0.005")))
    assert p.evaluate({"value": 1004.9}, r)[0] is True
    assert p.evaluate({"value": 1005.1}, r)[0] is False


def test_within_explanation_names_actual_op_target_and_band() -> None:
    c = claim(VALUE=("==", Decimal("12.5")), ABS_TOLERANCE=(None, Decimal("0.01")))
    ok, why = Within("value", "VALUE").evaluate({"value": Decimal("13")}, c)
    assert ok is False
    assert why == "value 13 violates == 12.5 (band 0.01)"


def test_within_relative_zero_target_is_exact() -> None:
    c = claim(VALUE=("==", Decimal(0)), TOLERANCE=(None, Decimal("0.01")))
    p = Within("value", "VALUE")
    assert p.evaluate({"value": 0}, c)[0] is True
    assert p.evaluate({"value": Decimal("0.0001")}, c)[0] is False


def test_within_tolerance_on_non_number_raises() -> None:
    c = claim(VALUE=("==", "a"), TOLERANCE=(None, Decimal("0.01")))
    with pytest.raises(TypeError):
        Within("value", "VALUE").evaluate({"value": "a"}, c)


# --- And / Or / Not truth tables -----------------------------------------------------

T3 = {True: TRUE, False: FALSE, None: NONE}


def _and(a: bool | None, b: bool | None) -> bool | None:
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    return True


def _or(a: bool | None, b: bool | None) -> bool | None:
    if a is True or b is True:
        return True
    if a is None or b is None:
        return None
    return False


@pytest.mark.parametrize("a", [True, False, None])
@pytest.mark.parametrize("b", [True, False, None])
def test_three_valued_truth_tables(a: bool | None, b: bool | None) -> None:
    assert ev(And(T3[a], T3[b])) is _and(a, b)
    assert ev(Or(T3[a], T3[b])) is _or(a, b)


@pytest.mark.parametrize(("a", "want"), [(True, False), (False, True), (None, None)])
def test_not(a: bool | None, want: bool | None) -> None:
    assert ev(Not(T3[a])) is want


def test_and_or_explanations_and_reads() -> None:
    ok, why = And(TRUE, NONE).evaluate(FACTS, NOCLAIM)
    assert ok is None and "n was not measured" in why
    ok, why = And(TRUE, FALSE, NONE).evaluate(FACTS, NOCLAIM)
    assert ok is False and why.startswith("f 0")
    p = And(Compare("a", "==", value=1), Or(Compare("b", "<", attr="X"), Not(TRUE)))
    assert p.reads() == ({"a", "b", "t"}, {"X"})
    with pytest.raises(ValueError):
        And()


def test_when_claim_has() -> None:
    p = WhenClaimHas("COVERAGE", Compare("coverage", ">=", attr="COVERAGE"), TRUE)
    with_cov = claim(COVERAGE=(">=", Decimal("0.5")))
    assert p.evaluate({"coverage": Decimal("0.4"), "t": 1}, with_cov)[0] is False
    assert p.evaluate({"coverage": Decimal("0.4"), "t": 1}, NOCLAIM)[0] is True
    assert p.reads() == ({"coverage", "t"}, {"COVERAGE"})
    opt = WhenClaimHas("COVERAGE", Compare("coverage", ">=", attr="COVERAGE"), Always())
    assert opt.evaluate({}, NOCLAIM)[0] is True
    assert opt.reads() == ({"coverage"}, {"COVERAGE"})
    with pytest.raises(TypeError):
        WhenClaimHas("COVERAGE", TRUE)  # type: ignore[call-arg]


def test_always() -> None:
    assert Always().evaluate({}, NOCLAIM)[0] is True
    assert Always().reads() == (set(), set())
    assert ev(And(Always(), NONE)) is None


def test_predicates_are_frozen_and_hashable() -> None:
    p = And(Compare("a", "==", value=1), Not(Within("v", "VALUE")))
    assert p == And(Compare("a", "==", value=1), Not(Within("v", "VALUE")))
    hash(p)
