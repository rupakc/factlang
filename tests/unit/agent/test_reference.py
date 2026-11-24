"""The prompt-ready verb reference is generated from the pack, so it cannot drift (spec 10)."""

from __future__ import annotations

import pytest

from factlang.agent import verb_reference
from factlang.contract import ContractPack
from factlang.diagnostics import DiagnosticBag
from factlang.ir import ColumnAggregate, Metric
from factlang.judge import Compare
from factlang.packs.standard import standard_pack
from factlang.semantics.analyzer import analyze
from factlang.semantics.catalog import NullCatalog
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import Operand
from factlang.syntax import parse

COMPACT_BUDGET = 6000  # must fit in one tool description


def _custom_pack() -> ContractPack:
    pack = standard_pack().extend("mine", "1.0")

    @pack.verb(
        "POSITIVE",
        summary="Every non-NULL value of the column is greater than zero.",
        operands=[Operand.column()],
        filterable=True,
    )
    def positive(claim: CheckedClaim) -> ColumnAggregate:
        return ColumnAggregate(claim.table, claim.column, (Metric.MIN_VALUE,), filter=claim.filter)

    @positive.derive
    def _(payload: object, claim: CheckedClaim) -> dict[str, object]:
        assert isinstance(payload, dict)
        return {"min": payload["min_value"]}

    positive.decide(Compare("min", ">", value=0))
    positive.facts("min")
    positive.example("POSITIVE orders.total", valid=True)
    positive.example("POSITIVE order_items.price WHERE order_id = 7", valid=True)
    positive.example("POSITIVE order_items.price WHERE order_id = 8", valid=True)
    positive.example("POSITIVE orders", valid=False, expect="FL302")
    positive.example("POSITIVE orders.total VALUE == 1", valid=False, expect="FL303")
    positive.example("POSITIVE", valid=False, expect="FL301")
    return pack


@pytest.mark.parametrize("style", ["markdown", "compact"])
def test_every_verb_and_every_valid_example_of_the_standard_pack(style: str) -> None:
    pack = standard_pack()
    text = verb_reference(pack, style=style)  # type: ignore[arg-type]
    for contract in pack.contracts():
        assert contract.name in text
        if style == "markdown":
            assert contract.spec.summary in text
            for ex in contract.examples:
                if ex.valid:
                    assert ex.source in text, ex.source


@pytest.mark.parametrize("style", ["markdown", "compact"])
def test_invalid_examples_never_appear(style: str) -> None:
    pack = standard_pack()
    text = verb_reference(pack, style=style)  # type: ignore[arg-type]
    lines = {line.strip().removeprefix("e.g. ") for line in text.splitlines()}
    for contract in pack.contracts():
        for ex in contract.examples:
            if not ex.valid:
                assert ex.source not in lines


def test_every_attribute_and_operator_is_documented() -> None:
    text = verb_reference(standard_pack(), style="markdown")
    for contract in standard_pack().contracts():
        for attr in contract.spec.attrs:
            assert attr.name in text and attr.type in text
            if attr.doc:
                assert attr.doc in text


def test_compact_reference_fits_a_tool_description() -> None:
    text = verb_reference(standard_pack(), style="compact")
    assert len(text) < COMPACT_BUDGET, len(text)


def test_compact_examples_are_themselves_valid_claims() -> None:
    pack = standard_pack()
    registry = pack.verb_registry()
    text = verb_reference(pack, style="compact")
    examples = [line.split("e.g. ", 1)[1] for line in text.splitlines() if "e.g. " in line]
    assert len(examples) >= len(pack.names())
    for source in examples:
        parsed = parse(source)
        bag = DiagnosticBag()
        bag.extend(parsed.diagnostics)
        claims = analyze(parsed.document, registry, catalog=NullCatalog(), bag=bag)
        assert len(claims) == 1 and not bag.has_errors, (source, bag.codes())


def test_default_pack_is_the_standard_pack_and_output_is_deterministic() -> None:
    assert verb_reference() == verb_reference(standard_pack())
    assert verb_reference(style="compact") == verb_reference(standard_pack(), style="compact")
    assert "standard" in verb_reference() and "1.0" in verb_reference()


def test_syntax_line_shows_operands_operators_optional_attrs_and_where() -> None:
    text = verb_reference(standard_pack(), style="compact")
    join = next(line for line in text.splitlines() if line.startswith("JOIN "))
    assert "<table.column> -> <table.column>" in join
    assert "MATCH >=|== <RATIO>" in join
    assert "[CARD <CARDINALITY>]" in join
    assert join.endswith("[WHERE <condition>]")
    exists = next(line for line in text.splitlines() if line.startswith("EXISTS "))
    assert exists.startswith("EXISTS <table>") and "WHERE" not in exists


def test_custom_verb_added_via_extend_appears() -> None:
    pack = _custom_pack()
    for style in ("markdown", "compact"):
        text = verb_reference(pack, style=style)  # type: ignore[arg-type]
        assert "POSITIVE <table.column> [WHERE <condition>]" in text
        assert "POSITIVE orders.total" in text
        # both styles carry the verb's summary (this used to be vacuous for compact)
        assert "Every non-NULL value of the column is greater than zero." in text
        assert "mine" in text
    # the parent pack is untouched
    assert "POSITIVE" not in verb_reference(standard_pack())


def test_removed_and_aliased_verbs_follow_the_pack() -> None:
    pack = standard_pack().extend("trim", "1.0")
    pack.remove("NOTE")
    pack.alias("CNT", "COUNT")
    text = verb_reference(pack, style="markdown")
    assert "NOTE " not in text
    assert "CNT opportunities VALUE ==1717" in text  # alias examples are renamed


def test_markdown_lists_constraints() -> None:
    text = verb_reference(standard_pack(), style="markdown")
    assert "`TOLERANCE` and `ABS_TOLERANCE` are mutually exclusive" in text
    assert "`WITH` and `TO` go together" in text


def test_unknown_style_raises() -> None:
    with pytest.raises(ValueError, match="style"):
        verb_reference(standard_pack(), style="html")  # type: ignore[arg-type]
