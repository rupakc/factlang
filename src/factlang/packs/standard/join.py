"""``JOIN a.x -> b.y MATCH >=99% [CARD N:1] [CAST f] [COVERAGE >=50%] [WITH [..] TO [..]]``:
key overlap of a join edge (spec 6.3, Amendment A R3/R4)."""

from __future__ import annotations

from collections.abc import Mapping

from factlang import ir
from factlang.contract import Contract, Example
from factlang.ir import KeyOverlap
from factlang.judge import Always, And, Compare, CompareUsingClaimOp, WhenClaimHas
from factlang.packs.standard._common import number, ratio
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.refs import ColumnRef, Ident
from factlang.semantics.signature import AttrSpec, Operand, VerbSpec

SPEC = VerbSpec(
    "JOIN",
    "The share of distinct left keys that find a right key satisfies the stated comparison.",
    (Operand.edge(),),
    attrs=(
        AttrSpec("MATCH", "RATIO", required=True, ops=(">=", "=="), doc="forward match rate"),
        AttrSpec("CARD", "CARDINALITY", doc="declared cardinality (N:1, 1:1, 1:N, N:M)"),
        AttrSpec("CAST", "TYPE_FAMILY", doc="compare both sides as this type family"),
        AttrSpec(
            "COVERAGE",
            "RATIO",
            ops=(">=", "==", "<=", "<", ">"),
            doc="share of distinct right keys reached",
        ),
        AttrSpec("WITH", "COLUMN_LIST", doc="further left key columns"),
        AttrSpec("TO", "COLUMN_LIST", doc="matching further right key columns"),
    ),
    filterable=True,
)

# CARD -> (left key bound, right key bound); ``None`` = unbounded.
_CARD_BOUNDS: dict[str, tuple[int | None, int | None]] = {
    "N:1": (None, 1),
    "1:1": (1, 1),
    "1:N": (1, None),
    "N:M": (None, None),
}

FACTS: tuple[str, ...] = (
    "match_rate",
    "coverage",
    "row_match_rate",
    "fan_in",
    "distinct_left",
    "distinct_right",
    "matched",
    "left_non_null",
    "matched_left_rows",
    "max_left_per_key",
    "max_right_per_key",
    "card_ok",
)


def _side(first: ColumnRef, claim: CheckedClaim, attr: str) -> tuple[ColumnRef, ...]:
    if not claim.has(attr):
        return (first,)
    extra = claim.value(attr)
    assert isinstance(extra, tuple) and all(isinstance(c, Ident) for c in extra)
    return (first, *(ColumnRef(first.table, c) for c in extra))


def plan(claim: CheckedClaim) -> ir.ProbePlan:
    edge = claim.edge
    cast = claim.value("CAST") if claim.has("CAST") else None
    assert cast is None or isinstance(cast, str)
    return KeyOverlap(
        _side(edge.left, claim, "WITH"),
        _side(edge.right, claim, "TO"),
        cast_to=cast,
        measure_multiplicity=claim.has("CARD"),
        filter=claim.filter,
    )


def _card_ok(
    claim: CheckedClaim, matched: object, max_left: object, max_right: object
) -> bool | None:
    """CARD consistent with the measured multiplicities; ``None`` when no key matched."""
    if not claim.has("CARD"):
        return None
    m = number(matched)
    if m is None or m == 0:
        return None
    left_bound, right_bound = _CARD_BOUNDS[str(claim.value("CARD"))]
    ok = True
    for bound, measured in ((left_bound, max_left), (right_bound, max_right)):
        if bound is None:
            continue
        value = number(measured)
        if value is None:
            return None
        ok = ok and value <= bound
    return ok


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    distinct_left = number(payload["distinct_left"])
    matched = number(payload["matched"])
    distinct_right = number(payload["distinct_right"])
    left_non_null = number(payload["left_non_null"])
    matched_left_rows = number(payload["matched_left_rows"])
    max_left = number(payload.get("max_left_per_key"))
    max_right = number(payload.get("max_right_per_key"))
    return {
        "match_rate": ratio(matched, distinct_left),
        "coverage": ratio(matched, distinct_right),
        "row_match_rate": ratio(matched_left_rows, left_non_null),
        "fan_in": ratio(left_non_null, distinct_left),
        "distinct_left": distinct_left,
        "distinct_right": distinct_right,
        "matched": matched,
        "left_non_null": left_non_null,
        "matched_left_rows": matched_left_rows,
        "max_left_per_key": max_left,
        "max_right_per_key": max_right,
        "card_ok": _card_ok(claim, matched, max_left, max_right),
    }


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=And(
        CompareUsingClaimOp("match_rate", "MATCH"),
        WhenClaimHas("COVERAGE", CompareUsingClaimOp("coverage", "COVERAGE"), Always()),
        WhenClaimHas("CARD", Compare("card_ok", "==", value=True), Always()),
    ),
    examples=(
        Example("JOIN order_items.order_id -> orders.id MATCH >=99%"),
        Example("JOIN order_items.order_id -> orders.id MATCH >= 99% CARD N:1 CAST string"),
        Example(
            "JOIN order_items.order_id -> orders.order_id WITH [shop_id] TO [shop_id] "
            "MATCH >=99% COVERAGE >= 50%"
        ),
        Example(
            "JOIN orders.customer_id -> customers.id MATCH == 100% COVERAGE < 1% "
            "WHERE status = 'paid'"
        ),
        Example("JOIN order_items.order_id -> orders.id CARD N:1", valid=False, expect="FL304"),
        Example(
            "JOIN order_items.order_id -> orders.order_id WITH [shop_id] MATCH >=99%",
            valid=False,
            expect="FL304",
        ),
        Example(
            "JOIN order_items.order_id -> orders.order_id WITH [shop_id, region] TO [shop_id] "
            "MATCH >=99%",
            valid=False,
            expect="FL309",
        ),
        Example("JOIN order_items.order_id -> orders.id MATCH <= 99%", valid=False, expect="FL306"),
        Example(
            "JOIN order_items.order_id -> orders.id MATCH >= 99% COVERAGE != 50%",
            valid=False,
            expect="FL306",
        ),
        Example("JOIN order_items.order_id MATCH >= 99%", valid=False, expect="FL302"),
    ),
    plan=plan,
    fact_names=FACTS,
)
