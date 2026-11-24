"""``FRESH t.c WITHIN 24h``: the newest value is at most the given age (spec 6.2)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from factlang import ir
from factlang.contract import Contract, Example
from factlang.ir import ColumnAggregate, Metric
from factlang.judge.predicate import Facts, Result, compare, fmt
from factlang.packs.standard._common import as_utc
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import AttrSpec, Operand, VerbSpec

SPEC = VerbSpec(
    "FRESH",
    "The newest value of the column is no older than the given duration.",
    (Operand.column(),),
    attrs=(AttrSpec("WITHIN", "DURATION", required=True, doc="largest allowed age"),),
    filterable=True,
)


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    return ColumnAggregate(
        claim.table, claim.column.column, (Metric.MAX_VALUE, Metric.NOW), filter=claim.filter
    )


def _age_seconds(max_value: object, server_now: object) -> Decimal | None:
    """``server_now - max_value`` in exact seconds; ``None`` if either is not temporal."""
    if not isinstance(max_value, dt.date) or not isinstance(server_now, dt.date):
        return None
    delta = as_utc(server_now) - as_utc(max_value)
    return Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 10**6


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    # ``now`` comes only from the warehouse (the ``server_now`` column), never from Python
    max_value = payload["max_value"]
    return {
        "age_seconds": _age_seconds(max_value, payload["server_now"]),
        "max_value": max_value,
    }


@dataclass(frozen=True)
class AgeWithin:
    """``age_seconds <= WITHIN``; says why when the age cannot be computed."""

    attr: str = "WITHIN"

    def evaluate(self, facts: Facts, claim: CheckedClaim) -> Result:
        age, newest = facts.get("age_seconds"), facts.get("max_value")
        if age is None:
            if newest is None:
                return None, "no non-NULL values: max_value was not measured"
            if not isinstance(newest, dt.date):
                return None, f"max_value {fmt(newest)} is not a temporal value"
            return None, "server_now was not measured"
        limit = claim.value(self.attr)
        ok = compare(age, "<=", limit)
        return ok, f"age_seconds {fmt(age)} {'<=' if ok else '>'} {fmt(limit)}"

    def reads(self) -> tuple[set[str], set[str]]:
        return {"age_seconds", "max_value"}, {self.attr}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=AgeWithin(),
    examples=(
        Example("FRESH orders.created_at WITHIN 24h"),
        Example("FRESH events.occurred_at WITHIN 15m WHERE kind = 'checkout'"),
        Example("FRESH opportunities.updated_on WITHIN 2d"),
        Example("FRESH orders.created_at", valid=False, expect="FL304"),
        Example("FRESH orders.created_at WITHIN 24", valid=False, expect="FL305"),
        Example("FRESH orders.created_at WITHIN <= 24h", valid=False, expect="FL306"),
        Example("FRESH orders WITHIN 24h", valid=False, expect="FL302"),
    ),
    plan=plan,
    fact_names=("age_seconds", "max_value"),
)
