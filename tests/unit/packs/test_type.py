"""TYPE (spec 6.1): catalog-only; an unknown catalog answer is NO_DATA."""

from __future__ import annotations

import pytest
from packs_helpers import Verdict, assert_pack_complete, build_claim, judge

from factlang.contract import ContractPack
from factlang.packs.standard.type import CONTRACT, catalog_facts
from factlang.semantics.catalog import NullCatalog, StaticCatalog

CAT = StaticCatalog({"orders": {"id": "INTEGER", "Total": "decimal"}})


def test_contract_is_complete() -> None:
    pack = ContractPack("t", "1")
    pack.register(CONTRACT)
    assert_pack_complete(pack)


def test_mode_and_spec() -> None:
    assert CONTRACT.plan is None and CONTRACT.catalog_facts is not None
    (attr,) = CONTRACT.spec.attrs
    assert (attr.name, attr.type, attr.required, attr.ops) == ("IS", "TYPE_FAMILY", True, ())


def test_facts_lowercase_family_as_written() -> None:
    c = build_claim("TYPE orders.id IS integer")
    assert catalog_facts(c, CAT) == {"actual_type": "integer", "family": "integer"}


def test_unquoted_column_matches_case_insensitively() -> None:
    c = build_claim("TYPE orders.total IS decimal")
    assert catalog_facts(c, CAT)["family"] == "decimal"


def test_quoted_column_matches_exactly() -> None:
    c = build_claim('TYPE orders."total" IS decimal')
    assert catalog_facts(c, CAT) == {"actual_type": None, "family": None}


def test_null_catalog_gives_none_facts() -> None:
    c = build_claim("TYPE orders.id IS integer")
    assert catalog_facts(c, NullCatalog()) == {"actual_type": None, "family": None}


@pytest.mark.parametrize(
    ("src", "verdict"),
    [
        ("TYPE orders.id IS integer", Verdict.PASS),
        ("TYPE orders.id IS string", Verdict.FAIL),
    ],
)
def test_decide(src: str, verdict: Verdict) -> None:
    facts = catalog_facts(build_claim(src), CAT)
    assert judge(CONTRACT, src, facts)[0] is verdict


def test_unknown_is_no_data_never_fail() -> None:
    src = "TYPE orders.id IS integer"
    facts = catalog_facts(build_claim(src), NullCatalog())
    assert judge(CONTRACT, src, facts)[0] is Verdict.NO_DATA
