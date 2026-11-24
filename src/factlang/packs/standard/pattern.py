"""``PATTERN t.c LIKE '%@%.%' RATE >=99%``: share of values matching a LIKE pattern (spec 6.2)."""

from __future__ import annotations

from collections.abc import Mapping

from factlang import ir
from factlang.contract import Contract, Example
from factlang.ir import PatternCoverage
from factlang.judge import CompareUsingClaimOp
from factlang.packs.standard._common import ratio
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import AttrSpec, Operand, VerbSpec

SPEC = VerbSpec(
    "PATTERN",
    "The share of non-NULL values matching the case-folded LIKE pattern satisfies the comparison.",
    (Operand.column(),),
    attrs=(
        AttrSpec("LIKE", "LIKE_PATTERN", required=True, doc="SQL LIKE pattern (% and _)"),
        AttrSpec(
            "RATE", "RATIO", required=True, ops=("==", "<=", "<", ">=", ">"), doc="match share"
        ),
    ),
    filterable=True,
)


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    pattern = claim.value("LIKE")
    assert isinstance(pattern, str)
    return PatternCoverage(claim.table, claim.column.column, pattern.lower(), filter=claim.filter)


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {"match_rate": ratio(payload["matching"], payload["non_null"])}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=CompareUsingClaimOp("match_rate", "RATE"),
    examples=(
        Example("PATTERN customers.email LIKE '%@%.%' RATE >=99%"),
        Example("PATTERN products.sku LIKE 'SKU-%' RATE == 100% WHERE active = TRUE"),
        Example("PATTERN customers.phone LIKE '+%' RATE < 5%"),
        Example("PATTERN customers.email LIKE '%@%'", valid=False, expect="FL304"),
        Example("PATTERN customers.email LIKE '%%' RATE >= 99%", valid=False, expect="FL309"),
        Example("PATTERN customers.email LIKE '%@%' RATE != 1", valid=False, expect="FL306"),
        Example("PATTERN customers.email LIKE 5 RATE >= 99%", valid=False, expect="FL305"),
    ),
    plan=plan,
    fact_names=("match_rate",),
)
