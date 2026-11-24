"""``UNIQUE t.c [WITH [c2, ...]]``: no key is held by more than one row (spec 6.2)."""

from __future__ import annotations

from collections.abc import Mapping

from factlang import ir
from factlang.contract import Contract, Example
from factlang.judge import Compare
from factlang.packs.standard._common import KEY_METRICS, key_plan, number
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import AttrSpec, Operand, VerbSpec

SPEC = VerbSpec(
    "UNIQUE",
    "No (composite) key value is held by more than one row.",
    (Operand.column(),),
    attrs=(AttrSpec("WITH", "COLUMN_LIST", doc="further key columns"),),
    filterable=True,
)


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    return key_plan(claim, KEY_METRICS)


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {
        "duplicate_keys": number(payload["duplicate_keys"]),
        "max_rows_per_key": number(payload["max_rows_per_key"]),
    }


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=Compare("max_rows_per_key", "<=", value=1),
    examples=(
        Example("UNIQUE orders.id"),
        Example("UNIQUE order_items.order_id WITH [line_no]"),
        Example("UNIQUE customers.email WHERE status = 'active'"),
        Example("UNIQUE orders", valid=False, expect="FL302"),
        Example("UNIQUE order_items.order_id WITH [order_id]", valid=False, expect="FL307"),
        Example("UNIQUE orders.id WITH []", valid=False, expect="FL309"),
        Example("UNIQUE orders.id WITH 3", valid=False, expect="FL305"),
    ),
    plan=plan,
    fact_names=("duplicate_keys", "max_rows_per_key"),
)
