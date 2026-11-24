"""``RANGE t.c BETWEEN [lo, hi]``: every non-NULL value lies in the closed range (spec 6.2)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal

from factlang import ir
from factlang.contract import Contract, Example
from factlang.ir import ColumnAggregate, Metric
from factlang.judge import Compare
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

_ONE_DAY = dt.timedelta(days=1)


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    return ColumnAggregate(
        claim.table,
        claim.column.column,
        (Metric.MIN_VALUE, Metric.MAX_VALUE),
        filter=claim.filter,
    )


def _incompatible(value: object, bounds: str) -> TypeError:
    return TypeError(f"cannot compare a measured {value!r} with {bounds} bounds")


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _in_range(lo_v: object, hi_v: object, lo: object, hi: object) -> bool:
    """``lo <= min`` and ``max <= hi`` under the kind rules of the bounds.

    Number bounds take numbers (exact ``Decimal``). DATE bounds take DATE values as is and
    TIMESTAMP values against the half-open ``[lo 00:00Z, hi+1d 00:00Z)``. TIMESTAMP bounds
    take timestamps in UTC (naive = UTC) and a DATE value as its midnight UTC. Any other
    pairing raises ``TypeError`` (judged ``ERROR``: a contract or claim defect)."""
    if isinstance(lo, Decimal) and isinstance(hi, Decimal):
        if not (_is_number(lo_v) and _is_number(hi_v)):
            raise _incompatible(lo_v if not _is_number(lo_v) else hi_v, "number")
        lmin, lmax = number(lo_v), number(hi_v)
        assert lmin is not None and lmax is not None
        return lo <= lmin and lmax <= hi
    if isinstance(lo, dt.datetime) and isinstance(hi, dt.datetime):
        for v in (lo_v, hi_v):
            if not isinstance(v, dt.date):
                raise _incompatible(v, "timestamp")
        assert isinstance(lo_v, dt.date) and isinstance(hi_v, dt.date)
        return lo <= as_utc(lo_v) and as_utc(hi_v) <= hi
    if isinstance(lo, dt.date) and isinstance(hi, dt.date):
        for v in (lo_v, hi_v):
            if not isinstance(v, dt.date):
                raise _incompatible(v, "date")
        assert isinstance(lo_v, dt.date) and isinstance(hi_v, dt.date)
        if isinstance(lo_v, dt.datetime) or isinstance(hi_v, dt.datetime):
            start, stop = as_utc(lo), as_utc(hi) + _ONE_DAY
            return start <= as_utc(lo_v) and as_utc(hi_v) < stop
        return lo <= lo_v and hi_v <= hi
    raise TypeError(f"unsupported range bounds {lo!r}, {hi!r}")


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    lo_v, hi_v = payload["min_value"], payload["max_value"]
    bounds = claim.value("BETWEEN")
    assert isinstance(bounds, tuple) and len(bounds) == 2
    in_range = None if lo_v is None or hi_v is None else _in_range(lo_v, hi_v, *bounds)
    return {"min_value": lo_v, "max_value": hi_v, "in_range": in_range}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=Compare("in_range", "==", value=True),
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
    fact_names=("min_value", "max_value", "in_range"),
)
