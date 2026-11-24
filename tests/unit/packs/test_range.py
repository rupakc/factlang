"""RANGE: ColumnAggregate(MIN_VALUE, MAX_VALUE), min >= lo and max <= hi (spec 6.2)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from packs_helpers import Verdict, assert_pack_complete, build_claim, judge

from factlang.contract import ContractPack, plan_for
from factlang.ir import ColumnAggregate, Metric
from factlang.packs.standard.range import CONTRACT

UTC = dt.timezone.utc
SRC = "RANGE order_items.quantity BETWEEN [0, 100]"


def test_contract_is_complete() -> None:
    pack = ContractPack("t", "1")
    pack.register(CONTRACT)
    assert_pack_complete(pack)


def test_attr_spec() -> None:
    (a,) = CONTRACT.spec.attrs
    assert (a.name, a.type, a.required, a.ops) == ("BETWEEN", "RANGE_PAIR", True, ())
    assert CONTRACT.spec.filterable


def test_plan() -> None:
    c = build_claim(SRC + " WHERE status = 'paid'")
    p = plan_for(CONTRACT, c)
    assert isinstance(p, ColumnAggregate) and p.column == c.column.column
    assert p.metrics == (Metric.MIN_VALUE, Metric.MAX_VALUE) and p.filter == c.filter


def run(src: str, lo: object, hi: object) -> tuple[Verdict, dict[str, object]]:
    v, _, f = judge(CONTRACT, src, {"min_value": lo, "max_value": hi})
    return v, f


@pytest.mark.parametrize(
    ("lo", "hi", "verdict"),
    [
        (0, 100, Verdict.PASS),
        (Decimal("0.5"), Decimal("99.5"), Verdict.PASS),
        (-1, 50, Verdict.FAIL),
        (1, 101, Verdict.FAIL),
        (1.5, 2.5, Verdict.PASS),
        (None, None, Verdict.NO_DATA),  # zero rows or all NULL
    ],
)
def test_numbers(lo: object, hi: object, verdict: Verdict) -> None:
    v, f = run(SRC, lo, hi)
    assert v is verdict
    if lo is not None:
        assert isinstance(f["min_value"], Decimal) and isinstance(f["max_value"], Decimal)


def test_dates() -> None:
    src = "RANGE orders.placed_on BETWEEN [DATE '2025-01-01', DATE '2025-12-31']"
    assert run(src, dt.date(2025, 1, 1), dt.date(2025, 12, 31))[0] is Verdict.PASS
    assert run(src, dt.date(2024, 12, 31), dt.date(2025, 6, 1))[0] is Verdict.FAIL


def test_timestamps_naive_measured_values_are_utc() -> None:
    src = (
        "RANGE orders.created_at BETWEEN "
        "[TIMESTAMP '2025-01-01T00:00:00Z', TIMESTAMP '2025-02-01T00:00:00Z']"
    )
    v, f = run(src, dt.datetime(2025, 1, 1), dt.datetime(2025, 1, 31, 23, 59))
    assert v is Verdict.PASS and f["min_value"] == dt.datetime(2025, 1, 1, tzinfo=UTC)
    aware = dt.timezone(dt.timedelta(hours=2))
    v2, _ = run(src, dt.datetime(2025, 1, 1, 1, 0, tzinfo=aware), dt.datetime(2025, 1, 2))
    assert v2 is Verdict.FAIL  # 01:00+02:00 is 23:00 UTC on Dec 31


def test_date_column_with_timestamp_bounds() -> None:
    src = (
        "RANGE orders.placed_on BETWEEN "
        "[TIMESTAMP '2025-01-01T00:00:00Z', TIMESTAMP '2025-02-01T00:00:00Z']"
    )
    assert run(src, dt.date(2025, 1, 1), dt.date(2025, 2, 1))[0] is Verdict.PASS


def test_kind_mismatch_is_error() -> None:
    assert run(SRC, "a", "z")[0] is Verdict.ERROR
