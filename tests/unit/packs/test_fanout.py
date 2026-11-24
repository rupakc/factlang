"""FANOUT: Compare MAX_PER_KEY against max_rows_per_key; WITH makes a composite key."""

from __future__ import annotations

from decimal import Decimal

import pytest
from packs_helpers import Verdict, assert_pack_complete, build_claim, judge

from factlang.contract import ContractPack, plan_for
from factlang.ir import ColumnAggregate, CompositeKeyAggregate, Metric
from factlang.packs.standard.fanout import CONTRACT
from factlang.semantics.refs import Ident

METRICS = (Metric.NON_NULL, Metric.DISTINCT_KEYS, Metric.MAX_PER_KEY, Metric.DUPLICATE_KEYS)


def test_contract_is_complete() -> None:
    pack = ContractPack("t", "1")
    pack.register(CONTRACT)
    assert_pack_complete(pack)


def test_attr_specs() -> None:
    by_name = {a.name: a for a in CONTRACT.spec.attrs}
    m = by_name["MAX_PER_KEY"]
    assert (m.type, m.required, m.ops) == ("INTEGER", True, ("<=", "<", "=="))
    assert (by_name["WITH"].type, by_name["WITH"].required) == ("COLUMN_LIST", False)


def test_plans() -> None:
    c = build_claim("FANOUT order_items.order_id MAX_PER_KEY <= 3 WHERE status = 'paid'")
    p = plan_for(CONTRACT, c)
    assert isinstance(p, ColumnAggregate) and p.metrics == METRICS and p.filter == c.filter
    w = plan_for(CONTRACT, build_claim("FANOUT order_items.order_id MAX_PER_KEY <= 3 WITH [sku]"))
    assert isinstance(w, CompositeKeyAggregate)
    assert w.columns == (Ident("order_id"), Ident("sku"))


def run(src: str, maxk: object) -> tuple[Verdict, dict[str, object]]:
    pl = {"non_null": 9, "distinct_keys": 3, "max_rows_per_key": maxk, "duplicate_keys": 2}
    v, _, facts = judge(CONTRACT, src, pl)
    return v, facts


@pytest.mark.parametrize(
    ("src", "maxk", "verdict"),
    [
        ("FANOUT order_items.order_id MAX_PER_KEY <= 3", 3, Verdict.PASS),
        ("FANOUT order_items.order_id MAX_PER_KEY <= 3", Decimal("4"), Verdict.FAIL),
        ("FANOUT order_items.order_id MAX_PER_KEY < 3", 3, Verdict.FAIL),
        ("FANOUT order_items.order_id MAX_PER_KEY == 1", 1, Verdict.PASS),
        ("FANOUT order_items.order_id MAX_PER_KEY = 1", 2, Verdict.FAIL),
        ("FANOUT order_items.order_id MAX_PER_KEY <= 3", None, Verdict.NO_DATA),
    ],
)
def test_decide(src: str, maxk: object, verdict: Verdict) -> None:
    assert run(src, maxk)[0] is verdict


def test_only_max_rows_per_key_is_a_fact() -> None:
    assert set(run("FANOUT order_items.order_id MAX_PER_KEY <= 3", 2)[1]) == {"max_rows_per_key"}
