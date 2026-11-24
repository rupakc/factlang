"""EXISTS (spec 6.1): table, or table COLUMN c; unknown catalog answer is NO_DATA."""

from __future__ import annotations

import pytest
from packs_helpers import Verdict, assert_pack_complete, build_claim, judge

from factlang.contract import ContractPack
from factlang.packs.standard.exists import CONTRACT, catalog_facts
from factlang.semantics.catalog import NullCatalog, StaticCatalog

CAT = StaticCatalog({"orders": {"id": "integer", "Amount": "decimal"}})


def test_contract_is_complete() -> None:
    pack = ContractPack("t", "1")
    pack.register(CONTRACT)
    assert_pack_complete(pack)


def test_mode_and_spec() -> None:
    assert CONTRACT.plan is None and CONTRACT.catalog_facts is not None
    (attr,) = CONTRACT.spec.attrs
    assert (attr.name, attr.type, attr.required, attr.ops) == ("COLUMN", "SYMBOL", False, ())
    assert not CONTRACT.spec.filterable


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("EXISTS orders", True),
        ("EXISTS shop.refunds", False),
        ("EXISTS refunds", False),
        ("EXISTS orders COLUMN id", True),
        ("EXISTS orders COLUMN amount", True),
        ("EXISTS orders COLUMN missing", False),
        ("EXISTS refunds COLUMN id", False),
    ],
)
def test_facts(src: str, expected: bool) -> None:
    assert catalog_facts(build_claim(src), CAT) == {"exists": expected}


@pytest.mark.parametrize("src", ["EXISTS orders", "EXISTS orders COLUMN id"])
def test_unknown_catalog_is_none_and_no_data(src: str) -> None:
    facts = catalog_facts(build_claim(src), NullCatalog())
    assert facts == {"exists": None}
    assert judge(CONTRACT, src, facts)[0] is Verdict.NO_DATA


def test_decide_pass_and_fail() -> None:
    assert judge(CONTRACT, "EXISTS orders", {"exists": True})[0] is Verdict.PASS
    assert judge(CONTRACT, "EXISTS orders", {"exists": False})[0] is Verdict.FAIL
