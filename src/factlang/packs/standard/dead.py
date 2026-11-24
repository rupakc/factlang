"""``DEAD t.c``: the column is entirely NULL (spec 6.2)."""

from __future__ import annotations

from collections.abc import Mapping

from factlang import ir
from factlang.contract import Contract, Example
from factlang.ir import ColumnAggregate, Metric
from factlang.judge import Compare
from factlang.packs.standard._common import null_rate
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import Operand, VerbSpec

SPEC = VerbSpec("DEAD", "Every value of the column is NULL.", (Operand.column(),))


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    return ColumnAggregate(claim.table, claim.column.column, (Metric.ROW_COUNT, Metric.NON_NULL))


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {"null_rate": null_rate(payload)}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=Compare("null_rate", "==", value=1),
    examples=(
        Example("DEAD orders.legacy_flag"),
        Example("DEAD shop.orders.legacy_flag"),
        Example("DEAD customers.fax"),
        Example("DEAD orders", valid=False, expect="FL302"),
        Example("DEAD orders.legacy_flag WHERE status = 'paid'", valid=False, expect="FL308"),
        Example("DEAD orders.legacy_flag RATE 1", valid=False, expect="FL303"),
    ),
    plan=plan,
    fact_names=("null_rate",),
)
