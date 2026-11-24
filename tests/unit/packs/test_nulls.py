"""NULLS: null_rate = (row_count - non_null) / row_count, None on 0 rows (spec 6.2)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from packs_helpers import Verdict, assert_pack_complete, build_claim, judge

from factlang.contract import ContractPack, plan_for
from factlang.ir import ColumnAggregate, Metric
from factlang.packs.standard.nulls import CONTRACT


def test_contract_is_complete() -> None:
    pack = ContractPack("t", "1")
    pack.register(CONTRACT)
    assert_pack_complete(pack)


def test_plan() -> None:
    c = build_claim("NULLS orders.email RATE <= 5% WHERE status = 'paid'")
    p = plan_for(CONTRACT, c)
    assert isinstance(p, ColumnAggregate)
    assert p.column == c.column.column and p.filter == c.filter
    assert p.metrics == (Metric.ROW_COUNT, Metric.NON_NULL)


def test_attr_spec() -> None:
    (attr,) = CONTRACT.spec.attrs
    assert (attr.type, attr.required, attr.ops) == ("RATIO", True, ("==", "<=", "<", ">=", ">"))


def rate(src: str, rows: object, non_null: object) -> tuple[Verdict, dict[str, object]]:
    v, _, facts = judge(CONTRACT, src, {"row_count": rows, "non_null": non_null})
    return v, facts


def test_normal() -> None:
    v, facts = rate("NULLS orders.email RATE <= 5%", 200, 195)
    assert v is Verdict.PASS and facts["null_rate"] == Decimal("0.025")


def test_fail() -> None:
    v, facts = rate("NULLS orders.email RATE <= 5%", 200, 180)
    assert v is Verdict.FAIL and facts["null_rate"] == Decimal("0.1")


def test_zero_rows_is_none_and_no_data() -> None:
    v, facts = rate("NULLS orders.email RATE <= 5%", 0, 0)
    assert v is Verdict.NO_DATA and facts["null_rate"] is None


def test_all_null() -> None:
    v, facts = rate("NULLS orders.email RATE == 100%", 7, 0)
    assert v is Verdict.PASS and facts["null_rate"] == Decimal(1)


def test_no_nulls_with_equality() -> None:
    v, facts = rate("NULLS orders.email RATE == 0", 7, 7)
    assert v is Verdict.PASS and facts["null_rate"] == Decimal(0)


@pytest.mark.parametrize("rows", [Decimal("1000"), 1000.0])
def test_decimal_and_float_payloads(rows: object) -> None:
    v, facts = rate("NULLS orders.email RATE >= 1%", rows, Decimal("990"))
    assert v is Verdict.PASS and facts["null_rate"] == Decimal("0.01")
