"""RANGE: ColumnAggregate(MIN_VALUE, MAX_VALUE) -> in_range (spec 6.2).

DATE bounds over TIMESTAMP values widen to the half-open [lo 00:00Z, hi+1d 00:00Z); a DATE
value against TIMESTAMP bounds is its midnight UTC; naive timestamps are UTC.
"""

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
DATE_SRC = "RANGE orders.placed_on BETWEEN [DATE '2025-01-01', DATE '2025-01-31']"
TS_SRC = (
    "RANGE orders.created_at BETWEEN "
    "[TIMESTAMP '2025-01-01T00:00:00Z', TIMESTAMP '2025-01-31T00:00:00Z']"
)


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
    assert (f["min_value"], f["max_value"]) == (lo, hi)  # facts are as measured
    assert f["in_range"] is {Verdict.PASS: True, Verdict.FAIL: False}.get(verdict)


def test_dates_over_dates() -> None:
    assert run(DATE_SRC, dt.date(2025, 1, 1), dt.date(2025, 1, 31))[0] is Verdict.PASS
    assert run(DATE_SRC, dt.date(2024, 12, 31), dt.date(2025, 1, 2))[0] is Verdict.FAIL
    assert run(DATE_SRC, dt.date(2025, 1, 2), dt.date(2025, 2, 1))[0] is Verdict.FAIL


@pytest.mark.parametrize(
    ("lo", "hi", "verdict"),
    [
        (
            dt.datetime(2025, 1, 1, tzinfo=UTC),
            dt.datetime(2025, 1, 31, 23, 59, 59, 999999),
            Verdict.PASS,
        ),
        (
            dt.datetime(2024, 12, 31, 23, 59, 59, 999999, tzinfo=UTC),
            dt.datetime(2025, 1, 2),
            Verdict.FAIL,
        ),
        (
            dt.datetime(2025, 1, 2),
            dt.datetime(2025, 2, 1, tzinfo=UTC),
            Verdict.FAIL,
        ),  # hi+1d excluded
        # 01:00+02:00 is 23:00 UTC on Dec 31
        (
            dt.datetime(2025, 1, 1, 1, 0, tzinfo=dt.timezone(dt.timedelta(hours=2))),
            dt.datetime(2025, 1, 5),
            Verdict.FAIL,
        ),
    ],
)
def test_date_bounds_over_timestamps(lo: object, hi: object, verdict: Verdict) -> None:
    assert run(DATE_SRC, lo, hi)[0] is verdict


@pytest.mark.parametrize(
    ("lo", "hi", "verdict"),
    [
        (dt.datetime(2025, 1, 1), dt.datetime(2025, 1, 31), Verdict.PASS),  # naive = UTC, closed
        (dt.datetime(2025, 1, 1), dt.datetime(2025, 1, 31, 0, 0, 0, 1), Verdict.FAIL),
        (dt.datetime(2024, 12, 31, 23, 59, 59, tzinfo=UTC), dt.datetime(2025, 1, 2), Verdict.FAIL),
    ],
)
def test_timestamps_over_timestamps(lo: object, hi: object, verdict: Verdict) -> None:
    assert run(TS_SRC, lo, hi)[0] is verdict


@pytest.mark.parametrize(
    ("lo", "hi", "verdict"),
    [
        (dt.date(2025, 1, 1), dt.date(2025, 1, 31), Verdict.PASS),  # midnight UTC on both ends
        (dt.date(2024, 12, 31), dt.date(2025, 1, 5), Verdict.FAIL),
        (dt.date(2025, 1, 2), dt.date(2025, 2, 1), Verdict.FAIL),
    ],
)
def test_timestamp_bounds_over_dates(lo: object, hi: object, verdict: Verdict) -> None:
    assert run(TS_SRC, lo, hi)[0] is verdict


@pytest.mark.parametrize(
    ("src", "lo", "hi"),
    [
        (SRC, "a", "z"),
        (SRC, dt.date(2025, 1, 1), dt.date(2025, 1, 2)),
        (DATE_SRC, 1, 2),
        (TS_SRC, "2025-01-01", "2025-01-02"),
        (SRC, True, True),
    ],
)
def test_incompatible_kinds_are_error(src: str, lo: object, hi: object) -> None:
    assert run(src, lo, hi)[0] is Verdict.ERROR
