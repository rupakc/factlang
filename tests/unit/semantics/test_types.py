from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from factlang.diagnostics import Span
from factlang.semantics import types as T
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef
from factlang.syntax.ast import Literal, LiteralKind

LK = LiteralKind
SP = Span(1, 1, 2)


def lit(kind: LiteralKind, value: object) -> Literal:
    return Literal(kind, value, SP)


def lst(*items: Literal) -> Literal:
    return lit(LK.LIST, tuple(items))


def num(s: str) -> Literal:
    return lit(LK.NUMBER, Decimal(s))


def run(name: str, literal: Literal) -> tuple[object, str | None]:
    vt = T.get_type(name)
    assert literal.kind in vt.accepts
    value = vt.coerce(literal) if vt.coerce else literal.value
    return value, (vt.validate(value) if vt.validate else None)


def ok(name: str, literal: Literal) -> object:
    value, err = run(name, literal)
    assert err is None, err
    return value


def bad(name: str, literal: Literal) -> str:
    _, err = run(name, literal)
    assert err
    return err


# --- refs ---------------------------------------------------------------------------


def test_refs() -> None:
    t = TableRef(None, Ident("s", False), Ident("orders", True))
    assert t.parts() == (Ident("s", False), Ident("orders", True))
    assert t.key() == (None, "s", "orders")
    assert Ident("a", False) != Ident("a", True)
    c = ColumnRef(t, Ident("id", False))
    assert EdgeRef(c, c).left == c
    full = TableRef(Ident("c", False), Ident("s", False), Ident("t", False))
    assert full.key() == ("c", "s", "t") and len(full.parts()) == 3


# --- registry -----------------------------------------------------------------------


def test_builtins_registered() -> None:
    for n in [
        "NUMBER",
        "INTEGER",
        "RATIO",
        "STRING",
        "SYMBOL",
        "CARDINALITY",
        "DURATION",
        "TEMPORAL",
        "NUMBER_OR_TEMPORAL",
        "TYPE_FAMILY",
        "COLUMN_LIST",
        "VALUE_LIST",
        "RANGE_PAIR",
        "SEPARATOR",
    ]:
        assert T.get_type(n).name == n


def test_register_duplicate_and_replace() -> None:
    vt = T.ValueType("X_TEST", frozenset({LK.NUMBER}))
    T.register_type(vt)
    try:
        with pytest.raises(ValueError):
            T.register_type(vt)
        vt2 = T.ValueType("X_TEST", frozenset({LK.STRING}))
        T.register_type(vt2, replace=True)
        assert T.get_type("X_TEST") is vt2
    finally:
        T._REGISTRY.pop("X_TEST", None)


def test_get_unknown() -> None:
    with pytest.raises(KeyError):
        T.get_type("NOPE")


# --- scalar types -------------------------------------------------------------------


def test_number() -> None:
    assert ok("NUMBER", num("1.5")) == Decimal("1.5")
    assert T.get_type("NUMBER").accepts == {LK.NUMBER}


def test_integer() -> None:
    v = ok("INTEGER", num("3"))
    assert v == 3 and isinstance(v, int)
    assert isinstance(ok("INTEGER", num("3.0")), int)
    bad("INTEGER", num("3.5"))
    assert ok("INTEGER", num("-2")) == -2


def test_ratio() -> None:
    assert ok("RATIO", lit(LK.PERCENT, Decimal("0.25"))) == Decimal("0.25")
    assert ok("RATIO", num("0")) == 0 and ok("RATIO", num("1")) == 1
    assert "ratio" in bad("RATIO", num("1.5")).lower()
    bad("RATIO", num("-0.1"))
    bad("RATIO", lit(LK.PERCENT, Decimal("1.01")))
    assert LK.STRING not in T.get_type("RATIO").accepts


def test_string_symbol() -> None:
    assert ok("STRING", lit(LK.STRING, "a b")) == "a b"
    assert LK.IDENT not in T.get_type("STRING").accepts
    assert ok("SYMBOL", lit(LK.IDENT, "foo")) == "foo"
    assert ok("SYMBOL", lit(LK.STRING, "bar")) == "bar"
    assert T.get_type("SYMBOL").accepts == {LK.IDENT, LK.STRING}


def test_cardinality_duration() -> None:
    assert ok("CARDINALITY", lit(LK.CARDINALITY, "1:N")) == "1:N"
    assert ok("DURATION", lit(LK.DURATION, 3600)) == 3600
    assert T.get_type("DURATION").accepts == {LK.DURATION}


def test_temporal() -> None:
    assert ok("TEMPORAL", lit(LK.DATE, "2025-01-31")) == dt.date(2025, 1, 31)
    v = ok("TEMPORAL", lit(LK.TIMESTAMP, "2025-01-31T12:00:00"))
    assert v == dt.datetime(2025, 1, 31, 12, tzinfo=dt.timezone.utc)
    z = ok("TEMPORAL", lit(LK.TIMESTAMP, "2025-01-31T12:00:00Z"))
    assert z == v and isinstance(z, dt.datetime) and z.utcoffset() == dt.timedelta(0)
    off = ok("TEMPORAL", lit(LK.TIMESTAMP, "2025-01-31T12:00:00+02:00"))
    assert off == dt.datetime(2025, 1, 31, 10, tzinfo=dt.timezone.utc)
    assert isinstance(off, dt.datetime) and off.utcoffset() == dt.timedelta(0)
    frac = ok("TEMPORAL", lit(LK.TIMESTAMP, "2025-01-31T12:00:00.5Z"))
    assert isinstance(frac, dt.datetime) and frac.microsecond == 500000
    ok("TEMPORAL", lit(LK.TIMESTAMP, "2025-01-31 12:00"))


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        (LK.DATE, "2025-02-30"),
        (LK.DATE, "2025-1-3"),
        (LK.DATE, "nope"),
        (LK.DATE, "2025-01-31T00:00:00"),
        (LK.TIMESTAMP, "2025-01-31T25:00:00"),
        (LK.TIMESTAMP, "garbage"),
        (LK.TIMESTAMP, "2025-01-31T12:00:00+99:00"),
    ],
)
def test_temporal_invalid(kind: LiteralKind, text: str) -> None:
    assert "ISO" in bad("TEMPORAL", lit(kind, text))


def test_number_or_temporal() -> None:
    assert ok("NUMBER_OR_TEMPORAL", num("2")) == Decimal("2")
    assert ok("NUMBER_OR_TEMPORAL", lit(LK.DATE, "2025-01-31")) == dt.date(2025, 1, 31)
    assert isinstance(
        ok("NUMBER_OR_TEMPORAL", lit(LK.TIMESTAMP, "2025-01-31T00:00:00Z")), dt.datetime
    )
    bad("NUMBER_OR_TEMPORAL", lit(LK.DATE, "2025-13-01"))
    assert LK.STRING not in T.get_type("NUMBER_OR_TEMPORAL").accepts


FAMILIES = "integer decimal float string boolean date timestamp time bytes json array struct other"


def test_type_family() -> None:
    assert len(T.TYPE_FAMILIES) == 13 and set(FAMILIES.split()) == set(T.TYPE_FAMILIES)
    for f in FAMILIES.split():
        assert ok("TYPE_FAMILY", lit(LK.IDENT, f)) == f
    assert ok("TYPE_FAMILY", lit(LK.STRING, "integer")) == "integer"
    assert "family" in bad("TYPE_FAMILY", lit(LK.IDENT, "varchar"))
    bad("TYPE_FAMILY", lit(LK.IDENT, "INTEGER"))


def test_column_list() -> None:
    v = ok("COLUMN_LIST", lst(lit(LK.IDENT, "a"), lit(LK.QIDENT, "B c")))
    assert v == (Ident("a", False), Ident("B c", True))
    bad("COLUMN_LIST", lst())
    bad("COLUMN_LIST", lst(lit(LK.IDENT, "a"), lit(LK.STRING, "x")))
    bad("COLUMN_LIST", lst(num("1")))
    assert T.get_type("COLUMN_LIST").accepts == {LK.LIST}
    assert LK.IDENT not in T.get_type("COLUMN_LIST").accepts


def test_value_list() -> None:
    v = ok(
        "VALUE_LIST",
        lst(
            num("1.50"),
            num("-0.0"),
            num("1E+3"),
            lit(LK.STRING, "x"),
            lit(LK.BOOL, True),
            lit(LK.BOOL, False),
            num("0.0001"),
        ),
    )
    assert v == ("1.5", "0", "1000", "x", "true", "false", "0.0001")
    assert ok("VALUE_LIST", lst(num("100"))) == ("100",)
    bad("VALUE_LIST", lst(lit(LK.IDENT, "a")))
    bad("VALUE_LIST", lst(lit(LK.DATE, "2025-01-01")))
    bad("VALUE_LIST", lst(lst(num("1"))))


def test_range_pair() -> None:
    v = ok("RANGE_PAIR", lst(num("1"), num("5")))
    assert v == (Decimal("1"), Decimal("5"))
    d = ok("RANGE_PAIR", lst(lit(LK.DATE, "2025-01-01"), lit(LK.DATE, "2025-02-01")))
    assert d == (dt.date(2025, 1, 1), dt.date(2025, 2, 1))
    bad("RANGE_PAIR", lst(num("1")))
    bad("RANGE_PAIR", lst(num("1"), num("2"), num("3")))
    bad("RANGE_PAIR", lst(lit(LK.STRING, "a"), lit(LK.STRING, "b")))
    bad("RANGE_PAIR", lst(num("1"), lit(LK.DATE, "2025-01-01")))
    bad("RANGE_PAIR", lst(lit(LK.DATE, "2025-02-30"), lit(LK.DATE, "2025-03-01")))


@pytest.mark.parametrize("sep", [":", "|", "; ", "-", " ", "(", ",", "~#=", "/>+"])
def test_separator_ok(sep: str) -> None:
    assert ok("SEPARATOR", lit(LK.STRING, sep)) == sep


@pytest.mark.parametrize("sep", ["", "abcd", "a", "::::", "\t", "é", ":a"])
def test_separator_bad(sep: str) -> None:
    bad("SEPARATOR", lit(LK.STRING, sep))
