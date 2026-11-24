from __future__ import annotations

import pytest

from factlang.semantics.signature import (
    AttrSpec,
    OperandKind,
    OperandSpec,
    VerbRegistry,
    VerbSpec,
)

T = OperandSpec("t", OperandKind.TABLE)


def spec(name: str = "UNIQUE", **kw: object) -> VerbSpec:
    return VerbSpec(name, "s", (T,), **kw)  # type: ignore[arg-type]


def test_defaults() -> None:
    a = AttrSpec("MAX", "RATIO")
    assert a.required is False and a.ops == () and a.doc == ""
    v = spec()
    assert v.attrs == () and v.filterable is False and v.requires == ()


def test_valid_spec() -> None:
    v = spec(
        attrs=(AttrSpec("MAX", "RATIO", ops=("<=", "<")), AttrSpec("KEY", "COLUMN_LIST")),
        requires=(("MAX", "KEY"),),
    )
    assert [a.name for a in v.attrs] == ["MAX", "KEY"]


@pytest.mark.parametrize("name", ["unique", "Unique", "", "1A", "A B"])
def test_bad_verb_name(name: str) -> None:
    with pytest.raises(ValueError, match="verb name"):
        spec(name)


@pytest.mark.parametrize("name", ["max", "", "M AX"])
def test_bad_attr_name(name: str) -> None:
    with pytest.raises(ValueError, match="attribute name"):
        spec(attrs=(AttrSpec(name, "NUMBER"),))


def test_duplicate_attr() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        spec(attrs=(AttrSpec("A", "NUMBER"), AttrSpec("A", "STRING")))


def test_where_reserved() -> None:
    with pytest.raises(ValueError, match="WHERE"):
        spec(attrs=(AttrSpec("WHERE", "NUMBER"),))


def test_registry() -> None:
    r = VerbRegistry()
    u = spec()
    r.register(u)
    r.register(spec("EXISTS"))
    assert r.get("UNIQUE") is u and r.get("NOPE") is None
    assert r.names() == ["EXISTS", "UNIQUE"]
    with pytest.raises(ValueError, match="already"):
        r.register(spec())


def test_suggest() -> None:
    r = VerbRegistry()
    r.register(spec())
    r.register(spec("EXISTS"))
    assert r.suggest("UNIQE") == "UNIQUE"
    assert r.suggest("EXIST") == "EXISTS"
    assert r.suggest("ZZZZZZ") is None
    assert VerbRegistry().suggest("UNIQE") is None


def test_requires_must_name_declared_attrs() -> None:
    with pytest.raises(ValueError, match="undeclared"):
        spec(attrs=(AttrSpec("A", "NUMBER"),), requires=(("A", "B"),))
    with pytest.raises(ValueError, match="undeclared"):
        spec(attrs=(AttrSpec("B", "NUMBER"),), requires=(("A", "B"),))


def test_duplicate_operand_names() -> None:
    with pytest.raises(ValueError, match="operand"):
        VerbSpec("JOIN", "s", (T, OperandSpec("t", OperandKind.COLUMN)))
