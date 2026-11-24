"""``FRESH t.c WITHIN 24h``: the newest value is at most the given age (spec 6.2)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping
from decimal import Decimal

from factlang import ir
from factlang.contract import Contract, Example
from factlang.ir import ColumnAggregate, Metric
from factlang.judge import Compare
from factlang.judge.predicate import fmt
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


def _age(max_value: object, server_now: object) -> tuple[Decimal | None, str | None]:
    """``(server_now - max_value in exact seconds, None)`` or ``(None, why not)``."""
    if max_value is None:
        return None, "no non-NULL values: max_value was not measured"
    if not isinstance(max_value, dt.date):
        return None, f"max_value {fmt(max_value)} is not a temporal value"
    if not isinstance(server_now, dt.date):
        return None, "server_now was not measured as a temporal value"
    delta = as_utc(server_now) - as_utc(max_value)
    seconds = Decimal(delta.days * 86400 + delta.seconds) + Decimal(delta.microseconds) / 10**6
    return seconds, None


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    # ``now`` comes only from the warehouse (the ``server_now`` column), never from Python
    age, note = _age(payload["max_value"], payload["server_now"])
    return {"age_seconds": age, "note": note}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=Compare("age_seconds", "<=", attr="WITHIN"),
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
    fact_names=("age_seconds", "note"),
)
