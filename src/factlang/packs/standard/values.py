"""``VALUES t.c IN ['a', 'b'] [SPLIT ':'] [ALLOW <=1%]``: column values are declared (spec 6.2)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from factlang import ir
from factlang.contract import Contract, Example
from factlang.ir import ValueMembership
from factlang.judge import Compare, WhenClaimHas
from factlang.judge.predicate import Facts, Result, compare, fmt
from factlang.packs.standard._common import number, ratio
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import AttrSpec, Operand, VerbSpec

SPEC = VerbSpec(
    "VALUES",
    "Every non-NULL value of the column is one of the declared values, as text.",
    (Operand.column(),),
    attrs=(
        AttrSpec("IN", "VALUE_LIST", required=True, doc="declared values (may be empty)"),
        AttrSpec("SPLIT", "SEPARATOR", doc="compare only the trimmed prefix before this"),
        AttrSpec("ALLOW", "RATIO", ops=("<=",), doc="tolerated share of undeclared rows"),
    ),
    filterable=True,
)


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    declared = claim.value("IN")
    assert isinstance(declared, tuple)
    split = claim.value("SPLIT") if claim.has("SPLIT") else None
    assert split is None or isinstance(split, str)
    return ValueMembership(
        claim.table, claim.column.column, declared, split=split, filter=claim.filter
    )


def _count(value: object) -> int | Decimal | None:
    n = number(value)
    if n is None:
        return None
    return int(n) if n == n.to_integral_value() else n


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    rows = payload["rows"]
    assert isinstance(rows, list)
    summaries = [r for r in rows if r.get("kind") == "summary"]
    if len(summaries) != 1:
        raise ValueError(f"expected exactly one summary row, got {len(summaries)}")
    (summary,) = summaries
    non_null, declared = number(summary["non_null"]), number(summary["declared_rows"])
    undeclared = (
        None if non_null is None or declared is None or non_null == 0 else non_null - declared
    )
    undeclared_rows = [r for r in rows if r.get("kind") == "undeclared"]
    # ranked by freq desc, value asc (spec 8.1); re-sorted so the fact never depends on
    # the order a connector happens to return rows in
    undeclared_rows.sort(key=lambda r: (-(number(r["freq"]) or 0), str(r["value"])))
    ranked = [{"value": r["value"], "count": _count(r["freq"])} for r in undeclared_rows]
    return {
        "undeclared_rows": _count(undeclared),
        "undeclared_distinct": _count(summary["undeclared_distinct"]),
        "top_undeclared": tuple(ranked),
        "coverage": ratio(declared, non_null),
    }


@dataclass(frozen=True)
class UndeclaredShareAtMost:
    """``1 - coverage <= ALLOW`` (the operator ``ALLOW`` takes is always ``<=``)."""

    fact: str = "coverage"
    attr: str = "ALLOW"

    def evaluate(self, facts: Facts, claim: CheckedClaim) -> Result:
        coverage = number(facts.get(self.fact))
        if coverage is None:
            return None, f"{self.fact} was not measured (no non-NULL values)"
        share = 1 - coverage
        allowed = claim.value(self.attr)
        ok = compare(share, "<=", allowed)
        return ok, f"undeclared share {fmt(share)} {'<=' if ok else '>'} {fmt(allowed)}"

    def reads(self) -> tuple[set[str], set[str]]:
        return {self.fact}, {self.attr}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=WhenClaimHas(
        "ALLOW", UndeclaredShareAtMost(), Compare("undeclared_rows", "==", value=0)
    ),
    examples=(
        Example("VALUES orders.status IN ['paid', 'open', 'void']"),
        Example("VALUES orders.reason IN ['other', 'fraud'] SPLIT ':' ALLOW <= 1%"),
        Example("VALUES order_items.quantity IN [1, 2, 3] WHERE status = 'paid'"),
        Example("VALUES orders.status IN []"),
        Example("VALUES orders.status", valid=False, expect="FL304"),
        Example("VALUES orders.status IN ['paid'] ALLOW >= 1%", valid=False, expect="FL306"),
        Example("VALUES orders.status IN ['paid'] SPLIT 'ab'", valid=False, expect="FL309"),
        Example("VALUES orders IN ['paid']", valid=False, expect="FL302"),
        Example("VALUES orders.status IN 'paid'", valid=False, expect="FL305"),
    ),
    plan=plan,
    fact_names=("undeclared_rows", "undeclared_distinct", "top_undeclared", "coverage"),
)
