"""``COUNT t VALUE ==1717 [TOLERANCE 0.5%]``: the row count (spec 6.4)."""

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
    "COUNT",
    "The number of rows in the table satisfies the stated comparison.",
    (Operand.table(),),
    attrs=answer_attrs("NUMBER"),
    filterable=True,
    exclusive=ANSWER_EXCLUSIVE,
)


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    return ColumnAggregate(claim.table, None, (Metric.ROW_COUNT,), filter=claim.filter)


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {"value": number(payload["row_count"])}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=Within("value", "VALUE"),
    examples=(
        Example("COUNT opportunities VALUE ==1717"),
        Example("COUNT opportunities VALUE == 1717 TOLERANCE 0.5% WHERE stage = 'Churned'"),
        Example("COUNT orders VALUE >= 1000 ABS_TOLERANCE 5"),
        Example("COUNT opportunities", valid=False, expect="FL304"),
        Example("COUNT VALUE == 1", valid=False, expect="FL301"),
        Example(
            "COUNT orders VALUE == 1 TOLERANCE 1% ABS_TOLERANCE 2", valid=False, expect="FL309"
        ),
        Example("COUNT orders VALUE == 1 TOLERANCE >= 1%", valid=False, expect="FL306"),
        Example("COUNT orders VALUE 1", valid=False, expect="FL306"),
        Example("COUNT orders VALUE == 'many'", valid=False, expect="FL305"),
    ),
    plan=plan,
    fact_names=("value",),
)
