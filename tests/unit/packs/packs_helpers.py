"""Shared helpers for the standard-pack verb tests."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "conformance"))

from test_contract_completeness import assert_pack_complete

from factlang.contract import Contract
from factlang.diagnostics import DiagnosticBag
from factlang.judge import Verdict, decide_facts
from factlang.packs.standard import standard_pack
from factlang.semantics.analyzer import analyze
from factlang.semantics.catalog import NullCatalog
from factlang.semantics.checked import CheckedClaim
from factlang.syntax import parse


def build_claim(source: str) -> CheckedClaim:
    parsed = parse(source)
    bag = DiagnosticBag()
    bag.extend(parsed.diagnostics)
    claims = analyze(
        parsed.document, standard_pack().verb_registry(), catalog=NullCatalog(), bag=bag
    )
    assert len(claims) == 1 and not bag.has_errors, bag.codes()
    return claims[0]


def judge(
    contract: Contract, source: str, payload: dict[str, object] | None
) -> tuple[Verdict, str, dict[str, object]]:
    """``(verdict, why, facts)`` of ``source`` over a hand-built probe payload."""
    return decide_facts(contract.derive, contract.decide, build_claim(source), payload)


__all__ = ["Verdict", "assert_pack_complete", "build_claim", "judge"]
