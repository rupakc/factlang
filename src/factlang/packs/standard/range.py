"""``RANGE t.c BETWEEN [lo, hi]``: every non-NULL value lies in the closed range (spec 6.2)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from factlang import ir
from factlang.contract import Contract, Example
from factlang.ir import ColumnAggregate, Metric
from factlang.judge.predicate import Facts, Result, compare, fmt
from factlang.packs.standard._common import as_utc, number
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import AttrSpec, Operand, VerbSpec

SPEC = VerbSpec(
    "RANGE",
    "The smallest and largest non-NULL values of the column lie within the closed range.",
    (Operand.column(),),
    attrs=(AttrSpec("BETWEEN", "RANGE_PAIR", required=True, doc="[low, high], inclusive"),),
    filterable=True,
)


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    return ColumnAggregate(
        claim.table,
        claim.column.column,
        (Metric.MIN_VALUE, Metric.MAX_VALUE),
        filter=claim.filter,
    )


def _measured(value: object, timestamp_bounds: bool) -> object:
    """Numbers as exact ``Decimal``; timestamps in UTC (naive = UTC); a DATE against
    TIMESTAMP bounds is its midnight UTC. Anything else is returned unchanged."""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return number(value)
    if isinstance(value, dt.datetime):
        return as_utc(value)
    if isinstance(value, dt.date) and timestamp_bounds:
        return as_utc(value)
    return value


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    bounds = claim.value("BETWEEN")
    assert isinstance(bounds, tuple) and len(bounds) == 2
    ts = isinstance(bounds[0], dt.datetime)
    return {
        "min_value": _measured(payload["min_value"], ts),
        "max_value": _measured(payload["max_value"], ts),
    }


@dataclass(frozen=True)
class WithinBounds:
    """``min_value >= low`` and ``max_value <= high`` of the claim's ``BETWEEN`` pair."""

    attr: str = "BETWEEN"

    def evaluate(self, facts: Facts, claim: CheckedClaim) -> Result:
        lo_v, hi_v = facts.get("min_value"), facts.get("max_value")
        if lo_v is None or hi_v is None:
            return None, "min_value/max_value were not measured (no non-NULL values)"
        bounds = claim.value(self.attr)
        assert isinstance(bounds, tuple)
        lo, hi = bounds
        if not compare(lo_v, ">=", lo):
            return False, f"min_value {fmt(lo_v)} < {fmt(lo)}"
        if not compare(hi_v, "<=", hi):
            return False, f"max_value {fmt(hi_v)} > {fmt(hi)}"
        return True, f"[{fmt(lo_v)}, {fmt(hi_v)}] within [{fmt(lo)}, {fmt(hi)}]"

    def reads(self) -> tuple[set[str], set[str]]:
        return {"min_value", "max_value"}, {self.attr}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=WithinBounds(),
    examples=(
        Example("RANGE order_items.quantity BETWEEN [0, 100]"),
        Example("RANGE products.price BETWEEN [0.01, 9999.99] WHERE active = TRUE"),
        Example("RANGE orders.placed_on BETWEEN [DATE '2020-01-01', DATE '2030-12-31']"),
        Example("RANGE order_items.quantity", valid=False, expect="FL304"),
        Example("RANGE order_items.quantity BETWEEN [100, 0]", valid=False, expect="FL309"),
        Example("RANGE order_items.quantity BETWEEN 'a'", valid=False, expect="FL305"),
        Example("RANGE order_items.quantity BETWEEN >= [0, 1]", valid=False, expect="FL306"),
        Example("RANGE orders BETWEEN [0, 1]", valid=False, expect="FL302"),
    ),
    plan=plan,
    fact_names=("min_value", "max_value"),
)
