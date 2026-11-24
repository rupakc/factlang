from __future__ import annotations

from decimal import Decimal

import pytest

from factlang.diagnostics import Span
from factlang.semantics.checked import CheckedAttr, CheckedClaim
from factlang.semantics.filters import (
    FAnd,
    FBetween,
    FBool,
    FCompare,
    FIn,
    FIsNull,
    FNot,
    FOr,
    Param,
    canonical,
)
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef


def tr(name: str) -> TableRef:
    return TableRef(None, None, Ident(name))


def P(v: object, fam: str, name: str = "p") -> Param:
    return Param(name, v, fam)


def eq(col: str, v: object, fam: str = "integer", name: str = "p") -> FCompare:
    return FCompare(Ident(col), "=", P(v, fam, name))


def test_and_or_commutative() -> None:
    a, b = eq("a", 1), eq("b", 2)
    assert canonical(FAnd((a, b))) == canonical(FAnd((b, a)))
    assert canonical(FOr((a, b))) == canonical(FOr((b, a)))
    assert canonical(FAnd((a, b))) != canonical(FOr((a, b)))


def test_negative_control_different_value_differs() -> None:
    assert canonical(eq("a", 1)) != canonical(eq("a", 2))
    assert canonical(eq("a", 1)) != canonical(eq("a", 1, "decimal"))


def test_independent_of_param_name_and_column_case() -> None:
    assert canonical(eq("A", 1, name="p1")) == canonical(eq("a", 1, name="zz"))
    assert canonical(FCompare(Ident("A", True), "=", P(1, "integer"))) != canonical(eq("a", 1))


def test_in_sorted_and_negation() -> None:
    x = FIn(Ident("a"), (P(2, "integer"), P(1, "integer")), False)
    y = FIn(Ident("a"), (P(1, "integer"), P(2, "integer")), False)
    assert canonical(x) == canonical(y)
    assert canonical(x) != canonical(FIn(Ident("a"), x.params, True))


def test_double_equals_normalised_and_bad_op() -> None:
    assert FCompare(Ident("a"), "==", P(1, "integer")).op == "="
    with pytest.raises(ValueError):
        FCompare(Ident("a"), "~", P(1, "integer"))


def test_other_nodes_distinct() -> None:
    nodes = [
        FIsNull(Ident("a")),
        FIsNull(Ident("a"), True),
        FBetween(Ident("a"), P(1, "integer"), P(2, "integer")),
        FNot(eq("a", 1)),
        eq("a", 1),
    ]
    assert len({canonical(n) for n in nodes}) == len(nodes)


def test_decimal_spelling_normalised() -> None:
    assert canonical(eq("a", Decimal("1.50"), "decimal")) == canonical(
        eq("a", Decimal("1.5"), "decimal")
    )


def test_bare_bool_canonical_equals_eq_true() -> None:
    assert canonical(FBool(Ident("deleted"))) == canonical(
        FCompare(Ident("deleted"), "=", Param("x", True, "boolean"))
    )
    assert canonical(FNot(FBool(Ident("d")))) == canonical(FNot(eq("d", True, "boolean")))
    assert canonical(FBool(Ident("d"))) != canonical(eq("d", False, "boolean"))


def claim(operands, attrs=None) -> CheckedClaim:
    return CheckedClaim(
        verb="V",
        operands=tuple(operands),
        attrs=attrs or {},
        filter=None,
        canonical_text="V x",
        semantic_hash="h",
        span=Span(1, 1, 2),
        source_text="V x",
        comment=None,
    )


def test_checked_helpers() -> None:
    col = ColumnRef(tr("t"), Ident("c"))
    edge = EdgeRef(ColumnRef(tr("l"), Ident("a")), ColumnRef(tr("r"), Ident("b")))
    c = claim(
        [col],
        {"n": CheckedAttr(">=", Decimal("2.5"), "number"), "i": CheckedAttr(None, 3, "number")},
    )
    assert c.table == tr("t") and c.column == col
    assert c.has("n") and not c.has("z")
    assert c.value("n") == Decimal("2.5") and c.op("n") == ">=" and c.op("i") is None
    assert c.number("i") == Decimal(3) and c.number("n") == Decimal("2.5")
    assert claim([edge]).table == tr("l") and claim([edge]).edge == edge
    assert claim([tr("t")]).table == tr("t")


def test_checked_errors() -> None:
    with pytest.raises(TypeError):
        _ = claim([tr("t")]).column
    with pytest.raises(TypeError):
        _ = claim([tr("t")]).edge
    with pytest.raises(TypeError):
        _ = claim([]).table
    with pytest.raises(TypeError):
        claim([tr("t")], {"s": CheckedAttr(None, "x", "string")}).number("s")
    with pytest.raises(TypeError):
        claim([tr("t")], {"s": CheckedAttr(None, True, "boolean")}).number("s")
    with pytest.raises(KeyError):
        claim([tr("t")]).value("nope")


def test_param_family_validated() -> None:
    with pytest.raises(ValueError):
        Param("p", 1, "number")
    for fam in ("integer", "decimal", "float", "string", "boolean", "date", "timestamp"):
        Param("p", 1, fam)


def test_nested_and_or_flatten_and_dedupe() -> None:
    a, b, c = eq("a", 1), eq("b", 2), eq("c", 3)
    assert canonical(FAnd((a, FAnd((b, c))))) == canonical(FAnd((FAnd((a, b)), c)))
    assert canonical(FOr((a, FOr((b, c))))) == canonical(FOr((FOr((a, b)), c)))
    assert canonical(FAnd((a, a))) == canonical(a)
    assert canonical(FOr((a, a))) == canonical(a)
    # negative controls: AND inside OR is not flattened; distinct items stay distinct
    assert canonical(FOr((a, FAnd((b, c))))) != canonical(FOr((a, b, c)))
    assert canonical(FAnd((a, b))) != canonical(a)


def test_in_dedupes_params() -> None:
    x = FIn(Ident("a"), (P(1, "integer"), P(1, "integer"), P(2, "integer")))
    y = FIn(Ident("a"), (P(1, "integer"), P(2, "integer")))
    assert canonical(x) == canonical(y)
    assert canonical(y) != canonical(FIn(Ident("a"), (P(1, "integer"),)))


def test_timestamp_normalised_to_utc() -> None:
    import datetime as dt

    naive = dt.datetime(2025, 1, 31, 12, 0)
    utc = dt.datetime(2025, 1, 31, 12, 0, tzinfo=dt.timezone.utc)
    plus2 = dt.datetime(2025, 1, 31, 14, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    other = dt.datetime(2025, 1, 31, 13, 0, tzinfo=dt.timezone.utc)
    c = [canonical(eq("t", v, "timestamp")) for v in (naive, utc, plus2)]
    assert len(set(c)) == 1
    assert canonical(eq("t", other, "timestamp")) != c[0]


def test_canonical_rejects_unknown_node() -> None:
    with pytest.raises(TypeError):
        canonical("nope")  # type: ignore[arg-type]


def test_attrs_immutable_and_hash() -> None:
    src = {"n": CheckedAttr(None, 1, "number")}
    c = claim([tr("t")], src)
    src["z"] = CheckedAttr(None, 2, "number")
    assert not c.has("z")
    with pytest.raises(TypeError):
        c.attrs["z"] = CheckedAttr(None, 2, "number")  # type: ignore[index]
    assert hash(c) == hash("h")
    assert c == claim([tr("t")], {"n": CheckedAttr(None, 1, "number")})
    assert len({c, claim([tr("t")], {"n": CheckedAttr(None, 1, "number")})}) == 1
