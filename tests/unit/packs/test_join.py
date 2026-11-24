"""JOIN: KeyOverlap -> match/coverage/row facts, CARD via card_ok (spec 6.3, Amendment A)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from packs_helpers import Verdict, assert_pack_complete, build_claim, judge

from factlang.contract import ContractPack, plan_for
from factlang.ir import KeyOverlap
from factlang.packs.standard.join import CONTRACT
from factlang.semantics.refs import ColumnRef, Ident

BASE = "JOIN order_items.order_id -> orders.id MATCH >= 99%"


def test_contract_is_complete() -> None:
    pack = ContractPack("t", "1")
    pack.register(CONTRACT)
    assert_pack_complete(pack)


def test_attr_specs() -> None:
    by = {a.name: a for a in CONTRACT.spec.attrs}
    assert (by["MATCH"].type, by["MATCH"].required, by["MATCH"].ops) == (
        "RATIO",
        True,
        (">=", "=="),
    )
    assert (by["CARD"].type, by["CARD"].required) == ("CARDINALITY", False)
    assert (by["CAST"].type, by["CAST"].required) == ("TYPE_FAMILY", False)
    cov = by["COVERAGE"]
    assert (cov.type, cov.required, cov.ops) == ("RATIO", False, (">=", "==", "<=", "<", ">"))
    assert (by["WITH"].type, by["TO"].type) == ("COLUMN_LIST", "COLUMN_LIST")
    assert CONTRACT.spec.filterable


def test_plan_single_key() -> None:
    c = build_claim(BASE + " WHERE status = 'paid'")
    p = plan_for(CONTRACT, c)
    assert isinstance(p, KeyOverlap)
    assert p.left == (c.edge.left,) and p.right == (c.edge.right,)
    assert p.cast_to is None and p.measure_multiplicity is False
    assert p.filter == c.filter and c.filter is not None and p.right_filter is None


def test_plan_composite_cast_card() -> None:
    c = build_claim(
        "JOIN order_items.order_id -> orders.order_id WITH [shop_id, region] "
        "TO [shop_ref, region] MATCH >= 99% CARD N:1 CAST string"
    )
    p = plan_for(CONTRACT, c)
    assert isinstance(p, KeyOverlap)
    lt, rt = c.edge.left.table, c.edge.right.table
    assert p.left == (
        c.edge.left,
        ColumnRef(lt, Ident("shop_id")),
        ColumnRef(lt, Ident("region")),
    )
    assert p.right == (
        c.edge.right,
        ColumnRef(rt, Ident("shop_ref")),
        ColumnRef(rt, Ident("region")),
    )
    assert p.cast_to == "string" and p.measure_multiplicity is True


def payload(**over: object) -> dict[str, object]:
    pl: dict[str, object] = {
        "distinct_left": 100,
        "matched": 99,
        "distinct_right": 200,
        "left_non_null": 400,
        "matched_left_rows": 396,
    }
    pl.update(over)
    return pl


def test_facts_exact() -> None:
    _, _, f = judge(CONTRACT, BASE, payload())
    assert f["match_rate"] == Decimal("0.99")
    assert f["coverage"] == Decimal("0.495")
    assert f["row_match_rate"] == Decimal("0.99")
    assert f["fan_in"] == Decimal(4)
    assert (f["distinct_left"], f["distinct_right"], f["matched"]) == (100, 200, 99)
    assert (f["left_non_null"], f["matched_left_rows"]) == (400, 396)
    assert f["card_ok"] is None  # no CARD written


def test_asymmetric_join_forward_full_reverse_tiny() -> None:
    v, _, f = judge(
        CONTRACT,
        BASE + " COVERAGE < 1%",
        payload(
            distinct_left=5, matched=5, distinct_right=1000, left_non_null=5, matched_left_rows=5
        ),
    )
    assert f["match_rate"] == 1 and f["coverage"] == Decimal("0.005")
    assert v is Verdict.PASS


def test_decimal_payload() -> None:
    v, _, f = judge(
        CONTRACT,
        BASE,
        payload(distinct_left=Decimal(100), matched=Decimal(99), left_non_null=Decimal(400)),
    )
    assert v is Verdict.PASS and f["match_rate"] == Decimal("0.99")


def test_zero_rows_every_ratio_is_none() -> None:
    v, _, f = judge(
        CONTRACT,
        BASE + " CARD N:1 COVERAGE >= 1%",
        {
            "distinct_left": 0,
            "matched": 0,
            "distinct_right": 0,
            "left_non_null": 0,
            "matched_left_rows": 0,
            "max_left_per_key": None,
            "max_right_per_key": None,
        },
    )
    assert v is Verdict.NO_DATA
    for k in ("match_rate", "coverage", "row_match_rate", "fan_in", "card_ok"):
        assert f[k] is None, k
    assert f["distinct_left"] == 0  # counts stay counts


def test_empty_right_side_coverage_none_not_zero() -> None:
    v, _, f = judge(
        CONTRACT,
        BASE + " COVERAGE >= 0%",
        payload(matched=0, distinct_right=0, matched_left_rows=0),
    )
    assert f["coverage"] is None and f["match_rate"] == 0
    assert v is Verdict.FAIL  # MATCH fails; FAIL beats undecidable


def test_all_null_left_keys() -> None:
    v, _, f = judge(
        CONTRACT, BASE, payload(distinct_left=0, matched=0, left_non_null=0, matched_left_rows=0)
    )
    assert v is Verdict.NO_DATA and f["match_rate"] is None and f["fan_in"] is None


@pytest.mark.parametrize(
    ("src", "matched", "verdict"),
    [
        (BASE, 99, Verdict.PASS),
        (BASE, 98, Verdict.FAIL),
        ("JOIN order_items.order_id -> orders.id MATCH == 100%", 100, Verdict.PASS),
        ("JOIN order_items.order_id -> orders.id MATCH = 1", 99, Verdict.FAIL),
        (BASE + " COVERAGE >= 50%", 99, Verdict.FAIL),  # coverage 0.495
        (BASE + " COVERAGE <= 50%", 99, Verdict.PASS),
    ],
)
def test_decide_match_and_coverage(src: str, matched: int, verdict: Verdict) -> None:
    assert judge(CONTRACT, src, payload(matched=matched))[0] is verdict


@pytest.mark.parametrize(
    ("card", "left", "right", "ok"),
    [
        ("N:1", 5, 1, True),
        ("N:1", 5, 2, False),
        ("1:1", 1, 1, True),
        ("1:1", 2, 1, False),
        ("1:1", 1, 2, False),
        ("1:N", 1, 7, True),
        ("1:N", 2, 1, False),
        ("N:M", 9, 9, True),
        ("N:1", 5, Decimal(1), True),
    ],
)
def test_card(card: str, left: object, right: object, ok: bool) -> None:
    v, _, f = judge(
        CONTRACT,
        f"{BASE} CARD {card}",
        payload(max_left_per_key=left, max_right_per_key=right),
    )
    assert f["card_ok"] is ok
    assert v is (Verdict.PASS if ok else Verdict.FAIL)


def test_card_none_when_no_keys_matched() -> None:
    v, _, f = judge(
        CONTRACT,
        "JOIN order_items.order_id -> orders.id MATCH >= 0% CARD N:1",
        payload(matched=0, matched_left_rows=0, max_left_per_key=None, max_right_per_key=None),
    )
    assert f["card_ok"] is None and v is Verdict.NO_DATA


def test_card_ignored_when_absent() -> None:
    v, _, _ = judge(CONTRACT, BASE, payload(max_left_per_key=9, max_right_per_key=9))
    assert v is Verdict.PASS
