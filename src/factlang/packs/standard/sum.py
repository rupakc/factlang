"""``SUM t.c VALUE ==1234.5 [TOLERANCE 1%]``: the sum of a column (spec 6.4)."""

from __future__ import annotations

from collections.abc import Mapping

from factlang import ir
from factlang.contract import Contract, Example
from factlang.ir import ColumnAggregate, Metric
from factlang.judge import Within
from factlang.packs.standard._common import (
    ANSWER_EXCLUSIVE,
    answer_attrs,
    number,
)
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import Operand, VerbSpec

SPEC = VerbSpec(
    "SUM",
    "The sum of the non-NULL values of the column satisfies the stated comparison.",
    (Operand.column(),),
    attrs=answer_attrs("NUMBER"),
    filterable=True,
    exclusive=ANSWER_EXCLUSIVE,
)


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    return ColumnAggregate(claim.table, claim.column.column, (Metric.SUM,), filter=claim.filter)


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {"value": number(payload["sum_value"])}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=Within("value", "VALUE"),
    examples=(
        Example("SUM order_items.price VALUE == 1234.5"),
        Example("SUM order_items.price VALUE >= 10 TOLERANCE 1% WHERE order_id = 7"),
        Example("SUM order_items.price VALUE == 99.99 ABS_TOLERANCE 0.01"),
        Example("SUM order_items VALUE == 1", valid=False, expect="FL302"),
        Example("SUM order_items.price", valid=False, expect="FL304"),
        Example(
            "SUM order_items.price VALUE == 1 TOLERANCE 1% ABS_TOLERANCE 1",
            valid=False,
            expect="FL309",
        ),
        Example("SUM order_items.price VALUE == 'x'", valid=False, expect="FL305"),
        Example("SUM order_items.price VALUE 1", valid=False, expect="FL306"),
        Example("SUM orders.created_at VALUE >= DATE '2025-01-31'", valid=False, expect="FL305"),
    ),
    plan=plan,
    fact_names=("value",),
)
