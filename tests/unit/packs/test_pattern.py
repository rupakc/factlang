"""PATTERN: PatternCoverage -> match_rate = matching / non_null (spec 6.2, 8.1)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from packs_helpers import Verdict, assert_pack_complete, build_claim, judge

from factlang.contract import ContractPack, plan_for
from factlang.ir import PatternCoverage
from factlang.packs.standard.pattern import CONTRACT

SRC = "PATTERN customers.email LIKE '%@%.%' RATE >= 99%"


def test_contract_is_complete() -> None:
    pack = ContractPack("t", "1")
    pack.register(CONTRACT)
    assert_pack_complete(pack)


def test_attr_specs() -> None:
    by = {a.name: a for a in CONTRACT.spec.attrs}
    assert (by["LIKE"].type, by["LIKE"].required, by["LIKE"].ops) == ("LIKE_PATTERN", True, ())
    assert (by["RATE"].type, by["RATE"].required) == ("RATIO", True)
    assert by["RATE"].ops == ("==", "<=", "<", ">=", ">")
    assert CONTRACT.spec.filterable


def test_plan_lowercases_pattern() -> None:
    c = build_claim("PATTERN products.sku LIKE 'SKU-%' RATE >= 90% WHERE active = TRUE")
    p = plan_for(CONTRACT, c)
    assert isinstance(p, PatternCoverage)
    assert p.pattern == "sku-%" and p.column == c.column.column and p.filter == c.filter
    assert c.value("LIKE") == "SKU-%"  # the claim keeps what the author wrote


@pytest.mark.parametrize(
    ("non_null", "matching", "verdict", "rate"),
    [
        (100, 99, Verdict.PASS, Decimal("0.99")),
        (Decimal(200), Decimal(190), Verdict.FAIL, Decimal("0.95")),
        (0, 0, Verdict.NO_DATA, None),  # zero rows or all NULL
    ],
)
def test_decide(non_null: object, matching: object, verdict: Verdict, rate: object) -> None:
    v, _, f = judge(CONTRACT, SRC, {"non_null": non_null, "matching": matching})
    assert v is verdict and f["match_rate"] == rate
