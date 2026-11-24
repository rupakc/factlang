"""``ROWS t VALUE >=1000``: the table's row count (spec 6.2)."""

from __future__ import annotations

from collections.abc import Mapping

from factlang import ir
from factlang.contract import Contract, Example
from factlang.ir import ColumnAggregate, Metric
from factlang.judge import CompareUsingClaimOp
from factlang.packs.standard._common import ALL_OPS, number
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import AttrSpec, Operand, VerbSpec

SPEC = VerbSpec(
    "ROWS",
    "The table's row count satisfies the stated comparison.",
    (Operand.table(),),
    attrs=(AttrSpec("VALUE", "NUMBER", required=True, ops=ALL_OPS, doc="row-count bound"),),
    filterable=True,
)


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    return ColumnAggregate(claim.table, None, (Metric.ROW_COUNT,), filter=claim.filter)


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {"row_count": number(payload["row_count"])}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=CompareUsingClaimOp("row_count", "VALUE"),
    examples=(
        Example("ROWS orders VALUE >=1000"),
        Example("ROWS orders VALUE == 0"),
        Example("ROWS order_items VALUE > 0 WHERE status = 'paid'"),
        Example("ROWS orders", valid=False, expect="FL304"),
        Example("ROWS orders VALUE 5", valid=False, expect="FL306"),
        Example("ROWS orders VALUE >= 'many'", valid=False, expect="FL305"),
        Example("ROWS VALUE >= 1", valid=False, expect="FL301"),
    ),
    plan=plan,
    fact_names=("row_count",),
)
