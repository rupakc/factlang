"""Expected verdicts and exact facts of every standard verb over the ``make_shop`` fixture.

Engine-neutral: Task 20 runs the same table on every ``verified`` engine. Each case names the
claim, its verdict and the facts it must report (a subset of the outcome's facts, compared
exactly; :class:`Approx` only where the fact depends on the wall clock).

Every verb has a PASS, a FAIL and an empty/undecidable case. For the counting verbs
(``ROWS``, ``COUNT``, ``DISTINCT``) an empty input is a *measured* zero, never ``NO_DATA``
(spec 6.2/6.4: a count of no rows is 0), so their ``empty`` case pins that 0 instead. The
catalog verbs reach ``NO_DATA`` when the catalog cannot answer (``catalog="null"``); ``NOTE``
is ``UNPROBEABLE`` by design.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal as D
from typing import Literal

from factlang.judge import Verdict


@dataclass(frozen=True)
class Approx:
    """A fact that must lie in ``[lo, hi]`` (wall-clock dependent facts only)."""

    lo: D
    hi: D

    def __eq__(self, other: object) -> bool:
        try:
            return self.lo <= D(str(other)) <= self.hi
        except Exception:
            return False

    def __hash__(self) -> int:
        return hash((self.lo, self.hi))


@dataclass(frozen=True)
class Case:
    verb: str
    kind: Literal["pass", "fail", "fail_table", "no_data", "empty", "unprobeable"]
    claim: str
    verdict: Verdict
    facts: Mapping[str, object] = field(default_factory=dict)
    catalog: Literal["warehouse", "null"] = "warehouse"

    @property
    def id(self) -> str:
        return f"{self.verb}-{self.kind}"


P, F, N = Verdict.PASS, Verdict.FAIL, Verdict.NO_DATA


def _r(n: int, d: int) -> D:
    return D(n) / D(d)


_JOIN_CUSTOMERS = {
    "match_rate": _r(10, 12),
    "coverage": D(1),
    "row_match_rate": _r(98, 100),
    "fan_in": _r(100, 12),
    "distinct_left": 12,
    "distinct_right": 10,
    "matched": 10,
    "left_non_null": 100,
    "matched_left_rows": 98,
}
_JOIN_COMPOSITE = {
    "match_rate": _r(2, 3),
    "coverage": _r(2, 3),
    "row_match_rate": _r(3, 4),
    "distinct_left": 3,
    "distinct_right": 3,
    "matched": 2,
    "left_non_null": 4,
    "matched_left_rows": 3,
}
_NO_JOIN = {
    "match_rate": None,
    "row_match_rate": None,
    "fan_in": None,
    "distinct_left": 0,
    "matched": 0,
}
_VALUES_FAIL = {
    "undeclared_rows": 3,
    "undeclared_distinct": 1,
    "top_undeclared": ({"value": "other: late", "count": 3},),
    "coverage": _r(97, 100),
    "undeclared_share": _r(3, 100),
}
_AGE = {"age_seconds": Approx(D(3600), D(3600 + 900))}

CASES: tuple[Case, ...] = (
    # -- structure (catalog) ---------------------------------------------------------------
    Case("EXISTS", "pass", "EXISTS orders COLUMN shipped_at", P, {"exists": True}),
    Case("EXISTS", "fail", "EXISTS orders COLUMN nosuch", F, {"exists": False}),
    Case("EXISTS", "fail_table", "EXISTS no_such_table", F, {"exists": False}),
    Case("EXISTS", "no_data", "EXISTS orders", N, {"exists": None}, catalog="null"),
    Case(
        "TYPE",
        "pass",
        "TYPE orders.amount IS decimal",
        P,
        {"actual_type": "decimal", "family": "decimal"},
    ),
    Case("TYPE", "fail", "TYPE orders.amount IS integer", F, {"family": "decimal"}),
    Case(
        "TYPE",
        "no_data",
        "TYPE orders.amount IS decimal",
        N,
        {"actual_type": None, "family": None},
        catalog="null",
    ),
    # -- data quality ----------------------------------------------------------------------
    Case("ROWS", "pass", "ROWS orders VALUE == 100", P, {"row_count": 100}),
    Case("ROWS", "fail", "ROWS orders VALUE >= 1000", F, {"row_count": 100}),
    Case("ROWS", "empty", "ROWS empty_t VALUE == 0", P, {"row_count": 0}),
    Case("NULLS", "pass", "NULLS orders.shipped_at RATE <= 5%", P, {"null_rate": _r(5, 100)}),
    Case("NULLS", "fail", "NULLS orders.shipped_at RATE < 5%", F, {"null_rate": _r(5, 100)}),
    Case("NULLS", "no_data", "NULLS empty_t.v RATE <= 5%", N, {"null_rate": None}),
    Case("DEAD", "pass", "DEAD all_null_t.v", P, {"null_rate": D(1)}),
    Case("DEAD", "fail", "DEAD orders.shipped_at", F, {"null_rate": _r(5, 100)}),
    Case("DEAD", "no_data", "DEAD empty_t.v", N, {"null_rate": None}),
    Case("UNIQUE", "pass", "UNIQUE customers.id", P, {"duplicate_keys": 0, "max_rows_per_key": 1}),
    Case(
        "UNIQUE",
        "fail",
        "UNIQUE orders.customer_id",
        F,
        {"duplicate_keys": 10, "max_rows_per_key": 10},
    ),
    Case("UNIQUE", "no_data", "UNIQUE all_null_t.v", N, {"max_rows_per_key": None}),
    Case(
        "FANOUT",
        "pass",
        "FANOUT order_items.order_id MAX_PER_KEY <= 3",
        P,
        {"max_rows_per_key": 3},
    ),
    Case(
        "FANOUT",
        "fail",
        "FANOUT order_items.order_id MAX_PER_KEY <= 2",
        F,
        {"max_rows_per_key": 3},
    ),
    Case("FANOUT", "no_data", "FANOUT empty_t.id MAX_PER_KEY <= 3", N, {"max_rows_per_key": None}),
    Case(
        "VALUES",
        "pass",
        "VALUES orders.status IN ['open', 'closed', 'churned', 'other'] SPLIT ':'",
        P,
        {"undeclared_rows": 0, "undeclared_distinct": 0, "coverage": D(1)},
    ),
    Case(
        "VALUES", "fail", "VALUES orders.status IN ['open', 'closed', 'churned']", F, _VALUES_FAIL
    ),
    Case(
        "VALUES",
        "no_data",
        "VALUES all_null_t.s IN ['a']",
        N,
        {"undeclared_rows": None, "coverage": None},
    ),
    Case(
        "RANGE",
        "pass",
        "RANGE orders.amount BETWEEN [0, 200]",
        P,
        {"min_value": D("1.25"), "max_value": D("100.25"), "in_range": True},
    ),
    Case(
        "RANGE",
        "fail",
        "RANGE orders.amount BETWEEN [0, 50]",
        F,
        {"min_value": D("1.25"), "max_value": D("100.25"), "in_range": False},
    ),
    Case("RANGE", "no_data", "RANGE all_null_t.v BETWEEN [0, 1]", N, {"in_range": None}),
    Case(
        "PATTERN",
        "pass",
        "PATTERN customers.email LIKE '%@%.%' RATE >= 90%",
        P,
        {"match_rate": D("0.9")},
    ),
    Case(
        "PATTERN",
        "fail",
        "PATTERN customers.email LIKE '%@%.%' RATE >= 99%",
        F,
        {"match_rate": D("0.9")},
    ),
    Case(
        "PATTERN", "no_data", "PATTERN all_null_t.s LIKE '%a%' RATE >= 1%", N, {"match_rate": None}
    ),
    Case("FRESH", "pass", "FRESH events.created_at WITHIN 2h", P, _AGE),
    Case("FRESH", "fail", "FRESH events.created_at WITHIN 30m", F, _AGE),
    Case("FRESH", "no_data", "FRESH empty_t.ts WITHIN 1h", N, {"age_seconds": None}),
    # -- relationships ---------------------------------------------------------------------
    Case(
        "JOIN", "pass", "JOIN orders.customer_id -> customers.id MATCH >= 80%", P, _JOIN_CUSTOMERS
    ),
    Case(
        "JOIN", "fail", "JOIN orders.customer_id -> customers.id MATCH >= 99%", F, _JOIN_CUSTOMERS
    ),
    Case("JOIN", "no_data", "JOIN empty_t.id -> customers.id MATCH >= 99%", N, _NO_JOIN),
    # -- answers ---------------------------------------------------------------------------
    Case("COUNT", "pass", "COUNT orders VALUE == 3 WHERE status = 'other: late'", P, {"value": 3}),
    Case("COUNT", "fail", "COUNT orders VALUE == 99", F, {"value": 100}),
    Case("COUNT", "empty", "COUNT empty_t VALUE == 0", P, {"value": 0}),
    Case("DISTINCT", "pass", "DISTINCT orders.customer_id VALUE == 12", P, {"value": 12}),
    Case("DISTINCT", "fail", "DISTINCT orders.customer_id VALUE == 10", F, {"value": 12}),
    Case("DISTINCT", "empty", "DISTINCT all_null_t.v VALUE == 0", P, {"value": 0}),
    Case("SUM", "pass", "SUM order_items.price VALUE == 366.30", P, {"value": D("366.30")}),
    Case("SUM", "fail", "SUM order_items.price VALUE == 366.31", F, {"value": D("366.30")}),
    Case("SUM", "no_data", "SUM all_null_t.v VALUE == 0", N, {"value": None}),
    Case("AVG", "pass", "AVG orders.amount VALUE == 50.75", P, {"value": D("50.75")}),
    Case("AVG", "fail", "AVG orders.amount VALUE == 50", F, {"value": D("50.75")}),
    Case("AVG", "no_data", "AVG empty_t.v VALUE == 0", N, {"value": None}),
    Case("MIN", "pass", "MIN orders.amount VALUE == 1.25", P, {"value": D("1.25")}),
    Case("MIN", "fail", "MIN orders.amount VALUE == 2", F, {"value": D("1.25")}),
    Case("MIN", "no_data", "MIN all_null_t.v VALUE == 0", N, {"value": None}),
    Case("MAX", "pass", "MAX orders.amount VALUE == 100.25", P, {"value": D("100.25")}),
    Case("MAX", "fail", "MAX orders.amount VALUE < 100", F, {"value": D("100.25")}),
    Case("MAX", "no_data", "MAX empty_t.v VALUE == 0", N, {"value": None}),
    # -- semantic --------------------------------------------------------------------------
    Case(
        "NOTE",
        "unprobeable",
        "NOTE order_items.price IS eur BECAUSE 'prices are stored in euro'",
        Verdict.UNPROBEABLE,
    ),
)

# Amendment A cases on the dedicated relationship fixtures.
COMPOSITE_JOIN: tuple[Case, ...] = (
    Case(
        "JOIN",
        "pass",
        "JOIN comp_left.a -> comp_right.a WITH [b] TO [b] MATCH >= 60%",
        P,
        _JOIN_COMPOSITE,
    ),
    Case(
        "JOIN",
        "fail",
        "JOIN comp_left.a -> comp_right.a WITH [b] TO [b] MATCH >= 99%",
        F,
        _JOIN_COMPOSITE,
    ),
    Case(
        "JOIN",
        "no_data",
        "JOIN comp_left.a -> comp_right.a WITH [b] TO [b] MATCH >= 60% WHERE a > 100",
        N,
        {"match_rate": None, "distinct_left": 0},
    ),
)
COVERAGE_FAIL = Case(
    "JOIN",
    "fail",
    "JOIN refs_child.k -> refs_parent.k MATCH == 100% COVERAGE >= 50%",
    F,
    {
        "match_rate": D(1),
        "coverage": _r(3, 1000),
        "distinct_left": 3,
        "distinct_right": 1000,
        "matched": 3,
        "left_non_null": 5,
        "fan_in": _r(5, 3),
    },
)

__all__ = ["CASES", "COMPOSITE_JOIN", "COVERAGE_FAIL", "Approx", "Case"]
