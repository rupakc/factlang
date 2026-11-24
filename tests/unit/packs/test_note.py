"""NOTE: an unprobeable semantic fact (spec 6.5)."""

from __future__ import annotations

import pytest
from packs_helpers import assert_pack_complete, build_claim

from factlang.contract import ContractPack
from factlang.packs.standard.note import CONTRACT


def test_contract_is_complete() -> None:
    pack = ContractPack("t", "1")
    pack.register(CONTRACT)
    assert_pack_complete(pack)


def test_mode_and_reason() -> None:
    assert CONTRACT.plan is None and CONTRACT.catalog_facts is None
    assert CONTRACT.unprobeable_reason == "semantic fact; recorded, not measured"
    assert CONTRACT.fact_names == ()
    assert not CONTRACT.spec.filterable


def test_attrs() -> None:
    attrs = {a.name: a for a in CONTRACT.spec.attrs}
    assert (attrs["IS"].type, attrs["IS"].required, attrs["IS"].ops) == ("SYMBOL", True, ())
    assert (attrs["BECAUSE"].type, attrs["BECAUSE"].required) == ("STRING", True)
    assert set(attrs) == {"IS", "BECAUSE"}


@pytest.mark.parametrize("unit", ["eur", "'net of tax'"])
def test_symbol_accepts_identifier_or_string(unit: str) -> None:
    c = build_claim(f"NOTE order_items.price IS {unit} BECAUSE 'finance confirmed'")
    assert c.value("BECAUSE") == "finance confirmed"
    assert isinstance(c.value("IS"), str)


def test_derive_is_empty() -> None:
    c = build_claim("NOTE order_items.price IS eur BECAUSE 'x'")
    assert CONTRACT.derive({}, c) == {}
