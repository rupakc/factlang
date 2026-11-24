"""DEAD: null_rate == 1; an empty table is NO_DATA (spec 6.2)."""

from __future__ import annotations

from decimal import Decimal

from packs_helpers import Verdict, assert_pack_complete, build_claim, judge

from factlang.contract import ContractPack, plan_for
from factlang.ir import ColumnAggregate, Metric
from factlang.packs.standard.dead import CONTRACT


def test_contract_is_complete() -> None:
    pack = ContractPack("t", "1")
    pack.register(CONTRACT)
    assert_pack_complete(pack)


def test_plan_matches_nulls_and_spec_has_no_attrs() -> None:
    c = build_claim("DEAD orders.legacy_flag")
    p = plan_for(CONTRACT, c)
    assert isinstance(p, ColumnAggregate)
    assert p.metrics == (Metric.ROW_COUNT, Metric.NON_NULL) and p.filter is None
    assert CONTRACT.spec.attrs == () and not CONTRACT.spec.filterable


def run(rows: object, non_null: object) -> tuple[Verdict, dict[str, object]]:
    v, _, f = judge(CONTRACT, "DEAD orders.legacy_flag", {"row_count": rows, "non_null": non_null})
    return v, f


def test_all_null_passes() -> None:
    v, facts = run(10, 0)
    assert v is Verdict.PASS and facts["null_rate"] == Decimal(1)


def test_some_values_fail() -> None:
    assert run(10, 1)[0] is Verdict.FAIL


def test_decimal_payload() -> None:
    assert run(Decimal("10"), Decimal("0"))[0] is Verdict.PASS


def test_empty_table_is_no_data() -> None:
    v, facts = run(0, 0)
    assert v is Verdict.NO_DATA and facts["null_rate"] is None
