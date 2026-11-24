"""Contract completeness (spec 1.1 items 3 and 13, 12 FL6xx, 19 B4 FL404).

``assert_pack_complete`` is run over every pack: decide reads only declared facts (FL600)
and declared attributes (FL601), each contract has >=3 valid and >=3 invalid examples that
(FL602) and behave as declared through ``parse`` + ``analyze`` with ``NullCatalog``, each
starting with its own verb (FL603), every decorator-built contract is complete (FL604), and
no plan samples its input (FL404). Each check has a negative control below.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from decimal import Decimal

import pytest

from factlang import ir
from factlang.contract import Contract, ContractPack, Example, check_pack
from factlang.ir import ColumnAggregate, Metric, SampleSpec
from factlang.judge import And, Compare, CompareUsingClaimOp
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import AttrSpec, Operand, VerbSpec


def assert_pack_complete(pack: ContractPack) -> None:
    problems = check_pack(pack)
    assert problems == [], "\n".join(problems)


def _plan(claim: CheckedClaim) -> ir.ProbePlan:
    return ColumnAggregate(claim.table, claim.column, (Metric.MIN_VALUE,))


def _derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {"min": payload["min_value"]}


EXAMPLES = (
    Example("POSITIVE orders.amount"),
    Example("POSITIVE orders.amount WHERE status = 'paid'"),
    Example("POSITIVE orders.amount AT_LEAST >=0"),
    Example("POSITIVE orders", valid=False, expect="FL302"),
    Example("POSITIVE orders.amount FOO 1", valid=False, expect="FL303"),
    Example("POSITIVE", valid=False, expect="FL301"),
)


def _good() -> Contract:
    return Contract(
        spec=VerbSpec(
            "POSITIVE",
            "Every value of the column is positive.",
            (Operand.column(),),
            attrs=(AttrSpec("AT_LEAST", "NUMBER", ops=(">=", ">")),),
            filterable=True,
        ),
        derive=_derive,
        decide=And(
            Compare("min", ">", value=0),
            CompareUsingClaimOp("min", "AT_LEAST"),
        ),
        examples=EXAMPLES,
        plan=_plan,
        fact_names=("min",),
    )


def _pack(c: Contract) -> ContractPack:
    pack = ContractPack("test", "1.0")
    pack.register(c)
    return pack


def _codes(problems: list[str]) -> list[str]:
    return [p.split(":", 1)[0] for p in problems]


def test_a_complete_pack_passes() -> None:
    assert_pack_complete(_pack(_good()))


def test_aliases_are_checked_through_their_target() -> None:
    pack = _pack(_good())
    pack.alias("POS", "POSITIVE")
    assert_pack_complete(pack)


def test_child_pack_checks_inherited_contracts() -> None:
    parent = _pack(replace(_good(), fact_names=()))
    child = parent.extend("mine", "1.0")
    assert _codes(check_pack(child)) == ["FL600"]


def test_fl600_decide_reads_a_fact_derive_never_produces() -> None:
    problems = check_pack(_pack(replace(_good(), fact_names=("minimum",))))
    assert _codes(problems) == ["FL600"]
    assert "POSITIVE" in problems[0] and "'min'" in problems[0]


def test_fl601_decide_reads_an_undeclared_attribute() -> None:
    bad = replace(_good(), decide=Compare("min", ">", attr="FLOOR"))
    problems = check_pack(_pack(bad))
    assert _codes(problems) == ["FL601"]
    assert "'FLOOR'" in problems[0]


@pytest.mark.parametrize("keep", [EXAMPLES[:2] + EXAMPLES[3:], EXAMPLES[:3] + EXAMPLES[4:], ()])
def test_fl602_too_few_examples(keep: tuple[Example, ...]) -> None:
    problems = check_pack(_pack(replace(_good(), examples=keep)))
    assert _codes(problems) == ["FL602"]


def test_fl603_valid_example_that_does_not_analyse() -> None:
    bad = replace(_good(), examples=(*EXAMPLES, Example("POSITIVE orders")))
    problems = check_pack(_pack(bad))
    assert _codes(problems) == ["FL603"]
    assert "FL302" in problems[0]


def test_fl603_valid_example_of_another_verb() -> None:
    pack = _pack(replace(_good(), examples=(*EXAMPLES, Example("OTHER orders.amount"))))
    other = replace(
        _good(),
        spec=replace(_good().spec, name="OTHER"),
        examples=tuple(replace(e, source=e.source.replace("POSITIVE", "OTHER")) for e in EXAMPLES),
    )
    pack.register(other)
    assert _codes(check_pack(pack)) == ["FL603"]


def test_fl603_invalid_example_with_the_wrong_code() -> None:
    bad = replace(
        _good(), examples=(*EXAMPLES, Example("POSITIVE orders", valid=False, expect="FL303"))
    )
    problems = check_pack(_pack(bad))
    assert _codes(problems) == ["FL603"]
    assert "FL303" in problems[0] and "FL302" in problems[0]


def test_fl603_invalid_example_that_is_valid() -> None:
    bad = replace(
        _good(),
        examples=(*EXAMPLES, Example("POSITIVE orders.amount", valid=False, expect="FL302")),
    )
    assert _codes(check_pack(_pack(bad))) == ["FL603"]


def test_fl603_plan_that_raises() -> None:
    def broken(claim: CheckedClaim) -> ir.ProbePlan:
        raise RuntimeError("boom")

    problems = check_pack(_pack(replace(_good(), plan=broken)))
    assert set(_codes(problems)) == {"FL603"}
    assert "boom" in problems[0]


def test_fl404_sampled_plan_negative_control() -> None:
    def sampled(claim: CheckedClaim) -> ir.ProbePlan:
        return ColumnAggregate(
            claim.table,
            claim.column,
            (Metric.MIN_VALUE,),
            sample=SampleSpec("bernoulli", Decimal("0.01")),
        )

    problems = check_pack(_pack(replace(_good(), plan=sampled)))
    assert set(_codes(problems)) == {"FL404"}
    assert len(problems) == 3  # one per valid example


def test_catalog_and_unprobeable_contracts_are_checked_too() -> None:
    def cat(claim: CheckedClaim, catalog: object) -> dict[str, object] | None:
        return None

    assert_pack_complete(_pack(replace(_good(), plan=None, catalog_facts=cat)))
    assert_pack_complete(_pack(replace(_good(), plan=None, unprobeable_reason="semantic")))

    def raising(claim: CheckedClaim, catalog: object) -> dict[str, object] | None:
        raise KeyError("x")

    assert set(_codes(check_pack(_pack(replace(_good(), plan=None, catalog_facts=raising))))) == {
        "FL603"
    }


def test_incomplete_builder_is_reported_not_raised() -> None:
    pack = ContractPack("test", "1.0")
    pack.verb("HALF", operands=[Operand.column()])
    problems = check_pack(pack)
    assert _codes(problems) == ["FL604"]
    assert "HALF" in problems[0] and "plan" in problems[0]


def test_incomplete_builder_does_not_hide_the_other_contracts() -> None:
    pack = _pack(replace(_good(), fact_names=()))
    pack.verb("HALF", operands=[Operand.column()])
    assert sorted(_codes(check_pack(pack))) == ["FL600", "FL604"]


def test_child_reports_the_parents_incomplete_builder_on_first_access() -> None:
    parent = ContractPack("base", "1.0")
    parent.verb("HALF", operands=[Operand.column()])
    child = parent.extend("mine", "1.0")
    child.register(_good())
    problems = check_pack(child)
    assert _codes(problems) == ["FL604"] and "HALF" in problems[0]


def test_fl603_example_must_start_with_its_own_verb() -> None:
    pack = _pack(
        replace(_good(), examples=(*EXAMPLES, Example("OTHER x", valid=False, expect="FL300")))
    )
    problems = check_pack(pack)
    assert _codes(problems) == ["FL603"] and "own verb" in problems[0]
    pack2 = _pack(replace(_good(), examples=(*EXAMPLES, Example("  -- POSITIVE orders.amount"))))
    assert _codes(check_pack(pack2)) == ["FL603"]


def test_alias_examples_pass_under_the_alias_name() -> None:
    from factlang.contract import check_contract

    pack = _pack(_good())
    pack.alias("POS", "POSITIVE")
    alias = pack.get("POS")
    assert alias is not None
    assert check_contract(alias, pack.verb_registry()) == []
