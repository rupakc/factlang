"""FRESH: age_seconds = server_now - max_value, UTC rules (spec 6.2)."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from packs_helpers import Verdict, assert_pack_complete, build_claim, judge

from factlang.contract import ContractPack, plan_for
from factlang.ir import ColumnAggregate, Metric
from factlang.packs.standard.fresh import CONTRACT

UTC = dt.timezone.utc
SRC = "FRESH orders.created_at WITHIN 24h"
NOW = dt.datetime(2026, 1, 2, 12, 0, tzinfo=UTC)


def test_contract_is_complete() -> None:
    pack = ContractPack("t", "1")
    pack.register(CONTRACT)
    assert_pack_complete(pack)


def test_attr_spec() -> None:
    (a,) = CONTRACT.spec.attrs
    assert (a.name, a.type, a.required, a.ops) == ("WITHIN", "DURATION", True, ())
    assert CONTRACT.spec.filterable


def test_plan() -> None:
    c = build_claim(SRC + " WHERE status = 'paid'")
    p = plan_for(CONTRACT, c)
    assert isinstance(p, ColumnAggregate) and p.column == c.column.column
    assert p.metrics == (Metric.MAX_VALUE, Metric.NOW) and p.filter == c.filter


def run(
    max_value: object, now: object = NOW, src: str = SRC
) -> tuple[Verdict, str, dict[str, object]]:
    return judge(CONTRACT, src, {"max_value": max_value, "server_now": now})


def test_aware_timestamp() -> None:
    v, _, f = run(dt.datetime(2026, 1, 2, 0, 0, tzinfo=UTC))
    assert v is Verdict.PASS and f["age_seconds"] == Decimal(43200)


def test_stale_fails() -> None:
    v, _, f = run(dt.datetime(2026, 1, 1, 11, 59, 59, 500000, tzinfo=UTC))
    assert v is Verdict.FAIL and f["age_seconds"] == Decimal("86400.5")


def test_exactly_at_bound_passes() -> None:
    assert run(dt.datetime(2026, 1, 1, 12, 0, tzinfo=UTC))[0] is Verdict.PASS


def test_naive_timestamp_is_utc() -> None:
    _, _, f = run(dt.datetime(2026, 1, 2, 11, 0), now=dt.datetime(2026, 1, 2, 12, 0))
    assert f["age_seconds"] == Decimal(3600)


def test_other_zone() -> None:
    plus2 = dt.timezone(dt.timedelta(hours=2))
    _, _, f = run(dt.datetime(2026, 1, 2, 13, 0, tzinfo=plus2))  # 11:00 UTC
    assert f["age_seconds"] == Decimal(3600)


def test_date_is_midnight_utc() -> None:
    _, _, f = run(dt.date(2026, 1, 2))
    assert f["age_seconds"] == Decimal(43200)


def test_empty_table_no_data() -> None:
    v, why, f = run(None)
    assert v is Verdict.NO_DATA and f["age_seconds"] is None and "no" in why


@pytest.mark.parametrize("value", ["2026-01-02", 17, Decimal("1.5")])
def test_not_temporal_is_no_data(value: object) -> None:
    v, why, f = run(value)
    assert v is Verdict.NO_DATA and f["age_seconds"] is None
    assert "not a temporal value" in why


def test_missing_server_now_is_no_data() -> None:
    v, _, f = run(dt.datetime(2026, 1, 2, tzinfo=UTC), now=None)
    assert v is Verdict.NO_DATA and f["age_seconds"] is None
