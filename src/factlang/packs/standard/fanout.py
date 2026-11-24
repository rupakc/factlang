"""``FANOUT t.c MAX_PER_KEY <=3 [WITH [...]]``: bound on rows per key (spec 6.2)."""

from __future__ import annotations

from collections.abc import Mapping

from factlang import ir
from factlang.contract import Contract, Example
from factlang.ir import Metric
from factlang.judge import CompareUsingClaimOp
from factlang.packs.standard._common import key_plan, number
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import AttrSpec, Operand, VerbSpec

SPEC = VerbSpec(
    "FANOUT",
    "The most rows held by any one (composite) key satisfies the stated comparison.",
    (Operand.column(),),
    attrs=(
        AttrSpec("MAX_PER_KEY", "INTEGER", required=True, ops=("<=", "<", "=="), doc="row bound"),
        AttrSpec("WITH", "COLUMN_LIST", doc="further key columns"),
    ),
    filterable=True,
)


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    return key_plan(claim, (Metric.MAX_PER_KEY,))


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {"max_rows_per_key": number(payload["max_rows_per_key"])}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=CompareUsingClaimOp("max_rows_per_key", "MAX_PER_KEY"),
    examples=(
        Example("FANOUT order_items.order_id MAX_PER_KEY <=3"),
        Example("FANOUT order_items.order_id MAX_PER_KEY < 10 WHERE status = 'paid'"),
        Example("FANOUT order_items.order_id MAX_PER_KEY == 1 WITH [product_id]"),
        Example("FANOUT order_items.order_id", valid=False, expect="FL304"),
        Example("FANOUT order_items.order_id MAX_PER_KEY >= 3", valid=False, expect="FL306"),
        Example("FANOUT order_items.order_id MAX_PER_KEY <= 2.5", valid=False, expect="FL309"),
        Example("FANOUT order_items MAX_PER_KEY <= 3", valid=False, expect="FL302"),
    ),
    plan=plan,
    fact_names=("max_rows_per_key",),
)
