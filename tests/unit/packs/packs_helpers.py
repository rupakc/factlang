"""Shared helpers for the standard-pack verb tests."""

from __future__ import annotations

from factlang.contract import Contract, ContractPack, check_pack
from factlang.diagnostics import DiagnosticBag
from factlang.judge import Verdict, decide_facts
from factlang.packs.standard import standard_pack
from factlang.semantics.analyzer import analyze
from factlang.semantics.catalog import NullCatalog
from factlang.semantics.checked import CheckedClaim
from factlang.syntax import parse


def assert_pack_complete(pack: ContractPack) -> None:
    """The completeness check of ``tests/conformance/test_contract_completeness.py``."""
    problems = check_pack(pack)
    assert problems == [], "\n".join(problems)


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
