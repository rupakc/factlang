"""ROWS: ColumnAggregate(ROW_COUNT) -> row_count, Compare VALUE (spec 6.2)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from packs_helpers import Verdict, assert_pack_complete, build_claim, judge

from factlang.contract import ContractPack, plan_for
from factlang.ir import ColumnAggregate, Metric
from factlang.packs.standard.rows import CONTRACT


def test_contract_is_complete() -> None:
    pack = ContractPack("t", "1")
    pack.register(CONTRACT)
    assert_pack_complete(pack)


def test_plan() -> None:
    c = build_claim("ROWS orders VALUE >= 1")
    p = plan_for(CONTRACT, c)
    assert isinstance(p, ColumnAggregate)
    assert (p.table, p.column, p.metrics, p.filter) == (c.table, None, (Metric.ROW_COUNT,), None)


def test_plan_carries_filter() -> None:
    c = build_claim("ROWS orders VALUE >= 1 WHERE status = 'paid'")
    p = plan_for(CONTRACT, c)
    assert isinstance(p, ColumnAggregate) and p.filter == c.filter and c.filter is not None


@pytest.mark.parametrize(
    ("src", "count", "verdict"),
    [
        ("ROWS orders VALUE >= 1000", 1500, Verdict.PASS),
        ("ROWS orders VALUE >= 1000", 999, Verdict.FAIL),
        ("ROWS orders VALUE == 0", 0, Verdict.PASS),  # zero rows is a measurement, not NO_DATA
        ("ROWS orders VALUE != 0", 0, Verdict.FAIL),
        ("ROWS orders VALUE < 10", Decimal("9"), Verdict.PASS),
        ("ROWS orders VALUE > 10", 10, Verdict.FAIL),
        ("ROWS orders VALUE <= 10", 10, Verdict.PASS),
        ("ROWS orders VALUE = 3", 3, Verdict.PASS),
        ("ROWS orders VALUE >= 1", None, Verdict.NO_DATA),
    ],
)
def test_decide(src: str, count: object, verdict: Verdict) -> None:
    v, _, facts = judge(CONTRACT, src, {"row_count": count})
    assert v is verdict
    assert facts["row_count"] == (None if count is None else Decimal(str(count)))


def test_explanation_names_the_number() -> None:
    _, why, _ = judge(CONTRACT, "ROWS orders VALUE >= 1000", {"row_count": 999})
    assert why == "row_count 999 < 1000"
