"""The standard pack scaffold (spec 6): fresh packs, completeness, registered verbs."""

from __future__ import annotations

from packs_helpers import assert_pack_complete

import factlang.packs.standard as standard
from factlang.contract import check_pack
from factlang.packs.standard import standard_pack


def test_pack_is_complete() -> None:
    assert_pack_complete(standard_pack())


def test_name_version_and_modules() -> None:
    pack = standard_pack()
    assert (pack.name, pack.version) == ("standard", "1.0")
    assert pack.names() == sorted(m.upper() for m in standard._MODULES)


def test_each_call_returns_a_fresh_pack() -> None:
    a, b = standard_pack(), standard_pack()
    assert a is not b
    a.remove("ROWS")
    assert "ROWS" in b.names() and "ROWS" not in a.names()
    assert check_pack(b) == []


def test_every_contract_has_a_one_sentence_summary() -> None:
    for c in standard_pack().contracts():
        assert c.spec.summary.endswith(".") and c.spec.summary.count(". ") == 0, c.name


def test_filterable_flags_follow_spec() -> None:
    filterable = {c.name for c in standard_pack().contracts() if c.spec.filterable}
    assert filterable == {
        "ROWS",
        "NULLS",
        "UNIQUE",
        "FANOUT",
        "VALUES",
        "RANGE",
        "PATTERN",
        "FRESH",
        "JOIN",
    }
