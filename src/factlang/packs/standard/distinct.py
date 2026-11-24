"""``DISTINCT t.c VALUE ==42``: distinct values of a column (spec 6.4)."""

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
    "DISTINCT",
    "The number of distinct non-NULL values of the column satisfies the stated comparison.",
    (Operand.column(),),
    attrs=answer_attrs("NUMBER"),
    filterable=True,
    exclusive=ANSWER_EXCLUSIVE,
)


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    return ColumnAggregate(
        claim.table, claim.column.column, (Metric.DISTINCT_KEYS,), filter=claim.filter
    )


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {"value": number(payload["distinct_keys"])}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=Within("value", "VALUE"),
    examples=(
        Example("DISTINCT orders.customer_id VALUE == 42"),
        Example("DISTINCT orders.status VALUE <= 10 WHERE created_at >= DATE '2025-01-01'"),
        Example("DISTINCT customers.country VALUE == 40 TOLERANCE 5%"),
        Example("DISTINCT orders VALUE == 42", valid=False, expect="FL302"),
        Example("DISTINCT orders.customer_id", valid=False, expect="FL304"),
        Example(
            "DISTINCT orders.customer_id VALUE == 4 TOLERANCE 1% ABS_TOLERANCE 1",
            valid=False,
            expect="FL309",
        ),
        Example("DISTINCT orders.customer_id VALUE >= 'x'", valid=False, expect="FL305"),
    ),
    plan=plan,
    fact_names=("value",),
)
