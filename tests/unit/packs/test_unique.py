"""UNIQUE: duplicate_keys and max_rows_per_key; passes when max_rows_per_key <= 1."""

from __future__ import annotations

from decimal import Decimal

from packs_helpers import Verdict, assert_pack_complete, build_claim, judge

from factlang.contract import ContractPack, plan_for
from factlang.ir import ColumnAggregate, CompositeKeyAggregate, Metric
from factlang.packs.standard.unique import CONTRACT
from factlang.semantics.refs import Ident

METRICS = (Metric.NON_NULL, Metric.DISTINCT_KEYS, Metric.MAX_PER_KEY, Metric.DUPLICATE_KEYS)


def test_contract_is_complete() -> None:
    pack = ContractPack("t", "1")
    pack.register(CONTRACT)
    assert_pack_complete(pack)


def test_single_column_plan() -> None:
    c = build_claim("UNIQUE orders.id WHERE status = 'paid'")
    p = plan_for(CONTRACT, c)
    assert isinstance(p, ColumnAggregate)
    assert (p.column, p.metrics, p.filter) == (c.column.column, METRICS, c.filter)


def test_with_switches_to_composite_plan() -> None:
    c = build_claim("UNIQUE order_items.order_id WITH [line_no, sku]")
    p = plan_for(CONTRACT, c)
    assert isinstance(p, CompositeKeyAggregate)
    assert p.columns == (Ident("order_id"), Ident("line_no"), Ident("sku"))
    assert p.metrics == METRICS


def payload(dup: object, maxk: object) -> dict[str, object]:
    return {"non_null": 5, "distinct_keys": 5, "max_rows_per_key": maxk, "duplicate_keys": dup}


def test_pass_when_no_duplicates() -> None:
    v, _, facts = judge(CONTRACT, "UNIQUE orders.id", payload(0, 1))
    assert v is Verdict.PASS and facts == {
        "duplicate_keys": Decimal(0),
        "max_rows_per_key": Decimal(1),
    }


def test_fail_names_numbers() -> None:
    v, why, facts = judge(CONTRACT, "UNIQUE orders.id", payload(Decimal("2"), Decimal("3")))
    assert v is Verdict.FAIL and facts["duplicate_keys"] == 2 and "3" in why


def test_empty_table_or_all_null_key_is_no_data() -> None:
    v, _, facts = judge(CONTRACT, "UNIQUE orders.id", payload(None, None))
    assert v is Verdict.NO_DATA and facts["max_rows_per_key"] is None


def test_zero_max_rows_passes() -> None:
    assert judge(CONTRACT, "UNIQUE orders.id", payload(0, 0))[0] is Verdict.PASS
