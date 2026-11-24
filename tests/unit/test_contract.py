"""Contracts, packs and the extension API (spec 1.1 item 3, 10, 19 B4)."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal

import pytest

import factlang.contract as contract_mod
from factlang import ir
from factlang.contract import Contract, ContractBuilder, ContractPack, Example, plan_for
from factlang.diagnostics import DiagnosticBag
from factlang.ir import ColumnAggregate, Metric, SampleSpec
from factlang.judge import Compare
from factlang.semantics.analyzer import analyze
from factlang.semantics.catalog import NullCatalog
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.refs import Ident
from factlang.semantics.signature import (
    AttrSpec,
    Operand,
    OperandKind,
    OperandSpec,
    VerbSpec,
)
from factlang.syntax import parse

COL = OperandSpec("column", OperandKind.COLUMN)


def _spec(name: str = "POSITIVE") -> VerbSpec:
    return VerbSpec(name, "Every value is positive.", (COL,), filterable=True)


def _plan(claim: CheckedClaim) -> ir.ProbePlan:
    return ColumnAggregate(claim.table, claim.column, (Metric.MIN_VALUE,), filter=claim.filter)


def _derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {"min": payload["min_value"]}


def _contract(name: str = "POSITIVE", **kw: object) -> Contract:
    args: dict[str, object] = {
        "spec": _spec(name),
        "derive": _derive,
        "decide": Compare("min", ">", value=0),
        "examples": (Example(f"{name} orders.amount"),),
        "plan": _plan,
        "fact_names": ("min",),
    }
    args.update(kw)
    return Contract(**args)  # type: ignore[arg-type]


def _claim(source: str, pack: ContractPack) -> CheckedClaim:
    bag = DiagnosticBag()
    claims = analyze(parse(source).document, pack.verb_registry(), catalog=NullCatalog(), bag=bag)
    assert not bag.has_errors, bag.codes()
    return claims[0]


# --- Example -----------------------------------------------------------------------------


def test_example_valid_defaults() -> None:
    e = Example("POSITIVE orders.amount")
    assert e.valid is True and e.expect is None


def test_invalid_example_requires_expect() -> None:
    with pytest.raises(ValueError, match="expect"):
        Example("POSITIVE orders", valid=False)
    assert Example("POSITIVE orders", valid=False, expect="FL302").expect == "FL302"


def test_example_expect_must_be_a_code() -> None:
    with pytest.raises(ValueError, match="FLnnn"):
        Example("POSITIVE orders", valid=False, expect="302")


# --- Contract ----------------------------------------------------------------------------


def test_contract_needs_exactly_one_mode() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _contract(plan=None)
    with pytest.raises(ValueError, match="exactly one"):
        _contract(unprobeable_reason="semantic")
    with pytest.raises(ValueError, match="exactly one"):
        _contract(plan=None, catalog_facts=lambda c, cat: None, unprobeable_reason="x")
    assert _contract(plan=None, catalog_facts=lambda c, cat: None).plan is None
    assert _contract(plan=None, unprobeable_reason="semantic").unprobeable_reason == "semantic"


def test_contract_rejects_empty_reason_and_bad_parts() -> None:
    with pytest.raises(ValueError, match="unprobeable_reason"):
        _contract(plan=None, unprobeable_reason="")
    with pytest.raises(TypeError, match="Predicate"):
        _contract(decide="min > 0")
    with pytest.raises(TypeError, match="Example"):
        _contract(examples=("POSITIVE orders.amount",))


def test_contract_normalises_sequences() -> None:
    c = _contract(examples=[Example("POSITIVE orders.amount")], fact_names=["min"])
    assert c.examples == (Example("POSITIVE orders.amount"),)
    assert c.fact_names == ("min",)
    assert c.name == "POSITIVE"


# --- ContractPack ------------------------------------------------------------------------


def test_register_get_names() -> None:
    pack = ContractPack("p", "1.0")
    c = _contract()
    pack.register(c)
    assert pack.get("POSITIVE") is c
    assert pack.get("NOPE") is None
    assert pack.names() == ["POSITIVE"]
    assert pack.contracts() == [c]
    assert (pack.name, pack.version) == ("p", "1.0")


def test_register_duplicate_raises() -> None:
    pack = ContractPack("p", "1.0")
    pack.register(_contract())
    with pytest.raises(ValueError, match="POSITIVE"):
        pack.register(_contract())


def test_override_records_previous() -> None:
    pack = ContractPack("p", "1.0")
    old, new = _contract(), _contract(decide=Compare("min", ">=", value=0))
    pack.register(old)
    pack.override(new)
    assert pack.get("POSITIVE") is new
    assert pack.overridden["POSITIVE"] is old


def test_override_of_unknown_raises() -> None:
    with pytest.raises(ValueError, match="register"):
        ContractPack("p", "1.0").override(_contract())


def test_alias_clones_spec_and_shares_parts() -> None:
    pack = ContractPack("p", "1.0")
    c = _contract()
    pack.register(c)
    pack.alias("POS", "POSITIVE")
    a = pack.get("POS")
    assert a is not None
    assert a.spec.name == "POS"
    assert a.spec.operands == c.spec.operands and a.spec.filterable == c.spec.filterable
    assert (a.derive, a.decide, a.plan) == (c.derive, c.decide, c.plan)
    assert a.examples == (Example("POS orders.amount"),)  # renamed to the alias verb
    assert pack.names() == ["POS", "POSITIVE"]
    assert pack.is_alias("POS") and not pack.is_alias("POSITIVE")
    # the alias is a verb the analyser accepts
    assert _claim("POS orders.amount", pack).verb == "POS"


def test_alias_resolves_at_get_so_it_follows_an_override() -> None:
    pack = ContractPack("p", "1.0")
    pack.register(_contract())
    pack.alias("POS", "POSITIVE")
    stricter = Compare("min", ">=", value=10)
    pack.override(_contract(decide=stricter))
    alias = pack.get("POS")
    assert alias is not None and alias.decide is stricter
    assert "POS" not in pack.overridden


def test_override_of_an_alias_replaces_the_alias() -> None:
    pack = ContractPack("p", "1.0")
    pack.register(_contract())
    pack.alias("POS", "POSITIVE")
    own = _contract("POS")
    pack.override(own)
    assert pack.get("POS") is own and not pack.is_alias("POS")
    prev = pack.overridden["POS"]
    assert prev.spec.name == "POS" and prev.decide == Compare("min", ">", value=0)


def test_alias_errors() -> None:
    pack = ContractPack("p", "1.0")
    pack.register(_contract())
    with pytest.raises(ValueError, match="NOPE"):
        pack.alias("X", "NOPE")
    with pytest.raises(ValueError, match="POSITIVE"):
        pack.alias("POSITIVE", "POSITIVE")
    with pytest.raises(ValueError, match="upper-case"):
        pack.alias("pos", "POSITIVE")


def test_alias_of_alias() -> None:
    pack = ContractPack("p", "1.0")
    pack.register(_contract())
    pack.alias("POS", "POSITIVE")
    pack.alias("P", "POS")
    p = pack.get("P")
    assert p is not None and p.spec.name == "P" and p.plan is _plan


def test_remove() -> None:
    pack = ContractPack("p", "1.0")
    pack.register(_contract())
    pack.alias("POS", "POSITIVE")
    pack.remove("POSITIVE")
    assert pack.get("POSITIVE") is None
    assert pack.get("POS") is None  # dangling alias resolves to nothing
    assert pack.names() == []
    with pytest.raises(KeyError):
        pack.remove("POSITIVE")
    pack.remove("POS")
    pack.register(_contract())  # name is free again
    assert pack.names() == ["POSITIVE"]


def test_verb_registry_is_fresh_each_call() -> None:
    pack = ContractPack("p", "1.0")
    pack.register(_contract())
    r1 = pack.verb_registry()
    assert r1 is not pack.verb_registry()
    assert r1.names() == ["POSITIVE"]
    pack.register(_contract("NEGATIVE"))
    assert r1.names() == ["POSITIVE"]
    assert pack.verb_registry().names() == ["NEGATIVE", "POSITIVE"]


def test_extend_child_sees_parent_and_is_isolated() -> None:
    parent = ContractPack("base", "1.0")
    base = _contract()
    parent.register(base)
    child = parent.extend("mine", "2.0")
    assert (child.name, child.version) == ("mine", "2.0")
    assert child.get("POSITIVE") is base
    child.register(_contract("NEGATIVE"))
    assert child.names() == ["NEGATIVE", "POSITIVE"]
    assert parent.names() == ["POSITIVE"]  # copy-on-write
    with pytest.raises(ValueError, match="POSITIVE"):
        child.register(_contract())  # visible through the parent


def test_child_override_and_remove_do_not_touch_parent() -> None:
    parent = ContractPack("base", "1.0")
    base = _contract()
    parent.register(base)
    parent.register(_contract("NEGATIVE"))
    child = parent.extend("mine", "1.0")
    mine = _contract(decide=Compare("min", ">=", value=0))
    child.override(mine)
    child.remove("NEGATIVE")
    assert child.get("POSITIVE") is mine and child.overridden["POSITIVE"] is base
    assert child.get("NEGATIVE") is None and child.names() == ["POSITIVE"]
    assert parent.get("POSITIVE") is base and parent.get("NEGATIVE") is not None
    assert parent.overridden == {}
    child.register(_contract("NEGATIVE"))  # removed in the child: free to register again
    assert child.get("NEGATIVE") is not parent.get("NEGATIVE")


def test_child_alias_of_parent_verb() -> None:
    parent = ContractPack("base", "1.0")
    parent.register(_contract())
    child = parent.extend("mine", "1.0")
    child.alias("POS", "POSITIVE")
    assert child.get("POS") is not None and parent.get("POS") is None


def test_child_sees_later_parent_registrations() -> None:
    parent = ContractPack("base", "1.0")
    child = parent.extend("mine", "1.0")
    parent.register(_contract())
    assert child.names() == ["POSITIVE"]


def test_merge_error_is_atomic() -> None:
    a, b = ContractPack("a", "1.0"), ContractPack("b", "1.0")
    a.register(_contract())
    b.register(_contract("NEGATIVE"))
    b.register(_contract())
    with pytest.raises(ValueError, match="POSITIVE"):
        a.merge(b)
    assert a.names() == ["POSITIVE"]  # nothing merged


def test_merge_keep_and_replace() -> None:
    mine, theirs = _contract(), _contract(decide=Compare("min", ">=", value=0))
    a, b = ContractPack("a", "1.0"), ContractPack("b", "1.0")
    a.register(mine)
    b.register(theirs)
    b.register(_contract("NEGATIVE"))
    a.merge(b, on_conflict="keep")
    assert a.get("POSITIVE") is mine and a.names() == ["NEGATIVE", "POSITIVE"]
    c = ContractPack("c", "1.0")
    c.register(mine)
    c.merge(b, on_conflict="replace")
    assert c.get("POSITIVE") is theirs and c.overridden["POSITIVE"] is mine
    with pytest.raises(ValueError, match="on_conflict"):
        c.merge(b, on_conflict="ignore")  # type: ignore[arg-type]


def test_merge_carries_aliases_as_aliases() -> None:
    a, b = ContractPack("a", "1.0"), ContractPack("b", "1.0")
    b.register(_contract())
    b.alias("POS", "POSITIVE")
    a.merge(b)
    assert a.is_alias("POS")
    alias = a.get("POS")
    assert alias is not None and alias.spec.name == "POS"


# --- decorator API -----------------------------------------------------------------------


def test_spec_section_10_decorator_example_runs_verbatim() -> None:
    import factlang as fl  # the real facade (Task 21); the body below is spec section 10

    pack = fl.standard_pack().extend("mine", "1.0")

    @pack.verb("POSITIVE", operands=[fl.Operand.column()], filterable=True)
    def positive(claim):  # type: ignore[no-untyped-def]  # returns the plan
        return fl.ir.ColumnAggregate(
            claim.table, claim.column, [fl.Metric.MIN_VALUE], filter=claim.filter
        )  # a filterable verb must pass it on (FL605)

    @positive.derive
    def _(payload, claim):  # type: ignore[no-untyped-def]
        return {"min": payload["min_value"]}

    positive.decide(fl.Compare("min", ">", value=0))
    positive.example("POSITIVE orders.amount", valid=True)

    assert isinstance(positive, ContractBuilder)
    c = pack.get("POSITIVE")
    assert c is not None and c.spec.filterable and c.spec.operands == (Operand.column(),)
    claim = _claim("POSITIVE orders.amount WHERE status = 'paid'", pack)
    plan = plan_for(c, claim)
    assert isinstance(plan, ColumnAggregate) and plan.metrics == (Metric.MIN_VALUE,)
    assert plan.filter is not None and plan.filter == claim.filter
    assert positive(claim) == plan  # the builder still calls the plan
    facts = c.derive({"min_value": Decimal("0.5")}, claim)
    assert facts == {"min": Decimal("0.5")}
    assert c.decide.evaluate(facts, claim)[0] is True
    assert c.examples == (Example("POSITIVE orders.amount"),)


def test_fl605_filterable_plan_that_drops_the_filter() -> None:
    pack = ContractPack("p", "1.0")
    pack.register(_contract(plan=lambda c: ColumnAggregate(c.table, c.column, (Metric.MIN_VALUE,))))
    c = pack.get("POSITIVE")
    assert c is not None and c.spec.filterable
    with pytest.raises(ValueError, match=r"^FL605 .*POSITIVE"):
        plan_for(c, _claim("POSITIVE orders.amount WHERE status = 'paid'", pack))
    # without a WHERE there is nothing to drop
    assert plan_for(c, _claim("POSITIVE orders.amount", pack)).filter is None


def _builder_pack() -> tuple[ContractPack, ContractBuilder]:
    pack = ContractPack("p", "1.0")
    b = pack.verb("POSITIVE", summary="Every value is positive.", operands=[Operand.column()])
    return pack, b


def test_incomplete_builder_raises_naming_the_missing_part() -> None:
    pack, b = _builder_pack()
    with pytest.raises(ValueError, match=r"POSITIVE.*plan"):
        pack.get("POSITIVE")
    b(_plan)
    with pytest.raises(ValueError, match=r"POSITIVE.*derive"):
        pack.names()
    b.derive(_derive)
    with pytest.raises(ValueError, match=r"POSITIVE.*decide"):
        pack.contracts()
    b.decide(Compare("min", ">", value=0))
    assert pack.names() == ["POSITIVE"]


def test_builder_parts_and_modes() -> None:
    pack, b = _builder_pack()
    b.derive(lambda p, c: {})  # returns the function, so it also works as a decorator
    b.unprobeable("semantic by design").decide(Compare("min", ">", value=0)).facts("min").example(
        "POSITIVE orders.amount"
    ).example("POSITIVE orders", valid=False, expect="FL302")
    c = pack.get("POSITIVE")
    assert c is not None
    assert c.unprobeable_reason == "semantic by design" and c.plan is None
    assert c.fact_names == ("min",)
    assert c.spec.summary == "Every value is positive."
    assert c.examples[1] == Example("POSITIVE orders", valid=False, expect="FL302")


def test_builder_catalog_mode_is_a_decorator() -> None:
    pack, b = _builder_pack()

    @b.catalog
    def facts(claim: CheckedClaim, catalog: object) -> dict[str, object] | None:
        return None

    b.derive(_derive)
    b.decide(Compare("min", ">", value=0))
    c = pack.get("POSITIVE")
    assert c is not None and c.catalog_facts is facts and c.plan is None


def test_builder_two_modes_raises() -> None:
    _, b = _builder_pack()
    b(_plan)
    with pytest.raises(ValueError, match="mode"):
        b.unprobeable("x")


def test_builder_after_registration_is_frozen() -> None:
    pack, b = _builder_pack()
    b(_plan)
    b.derive(_derive)
    b.decide(Compare("min", ">", value=0))
    pack.get("POSITIVE")
    with pytest.raises(RuntimeError, match="registered"):
        b.example("POSITIVE orders.amount")


def test_verb_name_clash_raises_early() -> None:
    pack = ContractPack("p", "1.0")
    pack.register(_contract())
    with pytest.raises(ValueError, match="POSITIVE"):
        pack.verb("POSITIVE", operands=[Operand.column()])
    pack.verb("NEW", operands=[Operand.column()])
    with pytest.raises(ValueError, match="NEW"):
        pack.verb("NEW", operands=[Operand.column()])
    with pytest.raises(ValueError, match="NEW"):
        pack.register(_contract("NEW"))


def test_verb_attrs_and_requires_pass_through() -> None:
    pack = ContractPack("p", "1.0")
    b = pack.verb(
        "BETWEENISH",
        operands=[Operand.column()],
        attrs=[AttrSpec("LO", "NUMBER"), AttrSpec("HI", "NUMBER")],
        requires=[("LO", "HI")],
    )
    b(_plan)
    b.derive(_derive)
    b.decide(Compare("min", ">", value=0))
    c = pack.get("BETWEENISH")
    assert c is not None
    assert [a.name for a in c.spec.attrs] == ["LO", "HI"]
    assert c.spec.requires == (("LO", "HI"),)


# --- plan_for / FL404 --------------------------------------------------------------------


def test_plan_for_rejects_a_sampled_plan() -> None:
    def sampled(claim: CheckedClaim) -> ir.ProbePlan:
        return ColumnAggregate(
            claim.table,
            claim.column,
            (Metric.MIN_VALUE,),
            sample=SampleSpec("system", Decimal("0.1")),
        )

    pack = ContractPack("p", "1.0")
    pack.register(_contract(plan=sampled))
    c = pack.get("POSITIVE")
    assert c is not None
    with pytest.raises(ValueError, match=r"^FL404"):
        plan_for(c, _claim("POSITIVE orders.amount", pack))


def test_plan_for_needs_a_plan_mode() -> None:
    pack = ContractPack("p", "1.0")
    pack.register(_contract(plan=None, unprobeable_reason="semantic"))
    c = pack.get("POSITIVE")
    assert c is not None
    with pytest.raises(ValueError, match="no plan"):
        plan_for(c, _claim("POSITIVE orders.amount", pack))


def test_plan_for_returns_an_exact_plan() -> None:
    pack = ContractPack("p", "1.0")
    pack.register(_contract())
    c = pack.get("POSITIVE")
    assert c is not None
    plan = plan_for(c, _claim("POSITIVE orders.amount", pack))
    assert isinstance(plan, ColumnAggregate) and plan.column == Ident("amount")


# --- Operand helpers + discovery ---------------------------------------------------------


def test_operand_helpers() -> None:
    assert Operand.table() == OperandSpec("table", OperandKind.TABLE)
    assert Operand.column() == OperandSpec("column", OperandKind.COLUMN)
    assert Operand.edge() == OperandSpec("edge", OperandKind.EDGE)
    assert Operand.column("key") == OperandSpec("key", OperandKind.COLUMN)


class _EP:
    def __init__(self, name: str, factory: object) -> None:
        self.name = name
        self._factory = factory
        self.loaded = 0

    def load(self) -> object:
        self.loaded += 1
        return self._factory


def test_discover_packs_reads_the_entry_point_group(monkeypatch: pytest.MonkeyPatch) -> None:
    good = _EP("mine", lambda: ContractPack("mine", "1.0"))
    bad = _EP("broken", lambda: "not a pack")
    seen: list[str] = []

    def fake_entry_points(*, group: str) -> list[_EP]:
        seen.append(group)
        return [good, bad]

    monkeypatch.setattr(contract_mod, "_entry_points", fake_entry_points)
    packs = contract_mod.discover_packs()
    assert seen == ["factlang.packs"]
    assert sorted(packs) == ["broken", "mine"]
    assert good.loaded == 0  # lazy: nothing imported until called
    assert packs["mine"]().name == "mine"
    with pytest.raises(TypeError, match="broken"):
        packs["broken"]()


def test_discover_packs_real_environment() -> None:
    assert isinstance(contract_mod.discover_packs(), dict)


# --- review round 1 ----------------------------------------------------------------------


def _parent_with_builder() -> ContractPack:
    parent = ContractPack("base", "1.0")

    @parent.verb("POSITIVE", operands=[Operand.column()])
    def positive(claim: CheckedClaim) -> ir.ProbePlan:
        return _plan(claim)

    positive.derive(_derive)
    positive.decide(Compare("min", ">", value=0))
    return parent


def test_child_sees_parent_builder_names_first() -> None:
    child = _parent_with_builder().extend("mine", "1.0")
    assert child.names() == ["POSITIVE"]


def test_child_sees_parent_builder_registry_first() -> None:
    child = _parent_with_builder().extend("mine", "1.0")
    assert child.verb_registry().names() == ["POSITIVE"]


def test_child_can_remove_parent_builder_first() -> None:
    parent = _parent_with_builder()
    child = parent.extend("mine", "1.0")
    child.remove("POSITIVE")
    assert child.names() == [] and parent.names() == ["POSITIVE"]


def test_child_cannot_register_over_a_pending_parent_builder() -> None:
    child = _parent_with_builder().extend("mine", "1.0")
    with pytest.raises(ValueError, match="POSITIVE"):
        child.register(_contract())


def test_child_read_raises_for_an_incomplete_parent_builder() -> None:
    parent = ContractPack("base", "1.0")
    parent.verb("HALF", operands=[Operand.column()])
    child = parent.extend("mine", "1.0")
    with pytest.raises(ValueError, match="HALF"):
        child.names()


def test_alias_examples_are_rewritten_and_cached() -> None:
    pack = ContractPack("p", "1.0")
    pack.register(
        _contract(
            examples=(
                Example("POSITIVE orders.amount"),
                Example("  POSITIVE orders", valid=False, expect="FL302"),
                Example("POSITIVEX orders.amount", valid=False, expect="FL300"),
            )
        )
    )
    pack.alias("POS", "POSITIVE")
    a = pack.get("POS")
    assert a is not None
    assert [e.source for e in a.examples] == [
        "POS orders.amount",
        "  POS orders",
        "POSITIVEX orders.amount",  # a different leading word is left alone
    ]
    assert pack.get("POS") is a  # cached
    pack.override(_contract(examples=(Example("POSITIVE orders.total"),)))
    b = pack.get("POS")
    assert b is not a and b is not None
    assert [e.source for e in b.examples] == ["POS orders.total"]


def test_inherited_alias_follows_a_child_override() -> None:
    parent = ContractPack("base", "1.0")
    parent.register(_contract())
    parent.alias("POS", "POSITIVE")
    child = parent.extend("mine", "1.0")
    stricter = Compare("min", ">=", value=10)
    child.override(_contract(decide=stricter))
    pos = child.get("POS")
    assert pos is not None and pos.decide is stricter
    parent_pos = parent.get("POS")
    assert parent_pos is not None and parent_pos.decide is not stricter
