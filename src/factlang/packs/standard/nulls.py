"""``NULLS t.c RATE <=5%``: the share of NULLs in a column (spec 6.2)."""

from __future__ import annotations

from collections.abc import Mapping

from factlang import ir
from factlang.contract import Contract, Example
from factlang.ir import ColumnAggregate, Metric
from factlang.judge import CompareUsingClaimOp
from factlang.packs.standard._common import null_rate
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import AttrSpec, Operand, VerbSpec

SPEC = VerbSpec(
    "NULLS",
    "The share of NULL values in the column satisfies the stated comparison.",
    (Operand.column(),),
    attrs=(
        AttrSpec(
            "RATE", "RATIO", required=True, ops=("==", "<=", "<", ">=", ">"), doc="NULL share"
        ),
    ),
    filterable=True,
)


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    return ColumnAggregate(
        claim.table, claim.column.column, (Metric.ROW_COUNT, Metric.NON_NULL), filter=claim.filter
    )


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {"null_rate": null_rate(payload)}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=CompareUsingClaimOp("null_rate", "RATE"),
    examples=(
        Example("NULLS orders.email RATE <=5%"),
        Example("NULLS orders.email RATE == 0"),
        Example("NULLS customers.phone RATE < 0.1 WHERE country = 'DE'"),
        Example("NULLS orders RATE <= 5%", valid=False, expect="FL302"),
        Example("NULLS orders.email", valid=False, expect="FL304"),
        Example("NULLS orders.email RATE != 5%", valid=False, expect="FL306"),
        Example("NULLS orders.email RATE <= 150%", valid=False, expect="FL309"),
    ),
    plan=plan,
    fact_names=("null_rate",),
)
