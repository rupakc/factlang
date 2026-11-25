"""Literal inlining fails closed (0.1.1 hardening).

The inline form is display-oriented, but a host may execute it when its driver cannot bind
parameters. So every value either becomes a literal that means exactly that value in every
native dialect, whatever the string-escape settings, or is refused with ``FL507``.
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

import pytest

from factlang.engine import string_quoter
from factlang.ir import Arity, ColumnAggregate, Metric
from factlang.semantics.filters import Param
from factlang.semantics.refs import Ident, TableRef
from factlang.sql import native_dialects
from factlang.sql.fragment import RenderedFragment
from factlang.sql.probe import RenderedProbe, RenderError, ansi_quote

sqlglot = pytest.importorskip("sqlglot")
from sqlglot import exp  # noqa: E402

from factlang.sql.registry import SQLGLOT_NAMES  # noqa: E402

PLAN = ColumnAggregate(TableRef(None, None, Ident("orders")), None, (Metric.ROW_COUNT,))
NATIVE = native_dialects()
HOSTILE_STRINGS = ["x\\' OR 1=1 --", "a\\", "a\\\\b", "nul\x00byte", "\x00"]


class Opaque:
    def __str__(self) -> str:
        return "x' OR 1=1 --"


NON_SCALARS: list[object] = [
    ["a", "b"],
    ("a",),
    {"k": "v"},
    {"a"},
    frozenset({1}),
    b"bytes",
    bytearray(b"x"),
    Opaque(),
    object(),
    dt.time(12, 0),
]
NON_FINITE = [math.nan, math.inf, -math.inf]


def probe(value: object, family: str, dialect: str = "duckdb") -> RenderedProbe:
    return RenderedProbe(
        "SELECT 1 AS x FROM t WHERE s = {{p1}}",
        (Param("p1", value, family),),
        Arity.SINGLE_ROW,
        PLAN,
        dialect,
        "verified",
    )


@pytest.mark.parametrize("dialect", NATIVE)
@pytest.mark.parametrize("text", HOSTILE_STRINGS)
def test_every_native_quoter_refuses_backslash_and_nul(dialect: str, text: str) -> None:
    with pytest.raises(RenderError, match="FL507"):
        string_quoter(dialect)(text)
    with pytest.raises(RenderError, match=r"FL507 cannot inline .* parameter p1"):
        probe(text, "string", dialect).inline(string_quoter(dialect))


@pytest.mark.parametrize("text", HOSTILE_STRINGS)
def test_default_and_custom_quoters_cannot_bypass_the_refusal(text: str) -> None:
    with pytest.raises(RenderError, match="FL507"):
        ansi_quote(text)
    with pytest.raises(RenderError, match=r"FL507 cannot inline .* parameter p1"):
        probe(text, "string").inline()
    with pytest.raises(RenderError, match=r"FL507 cannot inline .* parameter p1"):
        probe(text, "string").inline(lambda s: "'" + s + "'")


@pytest.mark.parametrize("dialect", NATIVE)
def test_no_native_quoter_uses_backslash_escaping(dialect: str) -> None:
    quote = string_quoter(dialect)
    try:
        out = quote("it's")
    except RenderError as exc:  # a dialect without '' doubling refuses instead
        assert str(exc).startswith("FL507")
        return
    assert out == "'it''s'"
    assert "\\" not in out


@pytest.mark.parametrize("dialect", NATIVE)
def test_every_quoter_literal_parses_back_to_the_same_value(dialect: str) -> None:
    quote = string_quoter(dialect)
    for text in ["it's", "x' OR 1=1 --", "''", "plain", '%_é"', "line\nbreak"]:
        try:
            lit = quote(text)
        except RenderError:
            continue
        tree = sqlglot.parse_one(f"SELECT {lit} AS x", read=SQLGLOT_NAMES[dialect])
        node = tree.expressions[0].this
        assert isinstance(node, exp.Literal) and node.is_string, (dialect, text, lit)
        assert node.this == text


@pytest.mark.parametrize("dialect", NATIVE)
@pytest.mark.parametrize("value", NON_SCALARS, ids=lambda v: type(v).__name__)
def test_non_scalar_values_are_refused(dialect: str, value: object) -> None:
    name = type(value).__name__
    for family in ("string", "integer", "decimal", "float", "boolean", "date", "timestamp"):
        with pytest.raises(RenderError, match=f"FL507 cannot inline {name} parameter p1"):
            probe(value, family, dialect).inline(string_quoter(dialect))


@pytest.mark.parametrize("dialect", NATIVE)
@pytest.mark.parametrize("value", NON_FINITE, ids=repr)
def test_non_finite_floats_are_refused(dialect: str, value: float) -> None:
    for family in ("float", "decimal", "string"):
        with pytest.raises(RenderError, match="FL507 cannot inline float parameter p1"):
            probe(value, family, dialect).inline(string_quoter(dialect))


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")])
def test_non_finite_decimals_are_refused(value: Decimal) -> None:
    with pytest.raises(RenderError, match="FL507 cannot inline Decimal parameter p1"):
        probe(value, "decimal").inline()


def test_boolean_family_takes_only_a_bool() -> None:
    assert probe(True, "boolean").inline().endswith("s = TRUE")
    with pytest.raises(RenderError, match="FL507 cannot inline str parameter p1"):
        probe("yes", "boolean").inline()


def test_fragment_inline_fails_closed_too() -> None:
    frag = RenderedFragment("s = {{p1}}", (Param("p1", ["a"], "string"),), "duckdb", "verified")
    with pytest.raises(RenderError, match="FL507 cannot inline list parameter p1"):
        frag.inline()
    frag = RenderedFragment("s = {{p1}}", (Param("p1", "a\\b", "string"),), "duckdb", "verified")
    with pytest.raises(RenderError, match="FL507"):
        frag.inline()


def _number(node: exp.Expression) -> float:
    if isinstance(node, exp.Neg):
        return -_number(node.this)
    assert isinstance(node, exp.Literal) and not node.is_string, repr(node)
    return float(node.this)


FINITE = [0.0, -0.0, 1.5, -2.25, 1e-07, 1e16, 1.5e300, -2.5e-10, 5e-324, 1.7976931348623157e308]


@pytest.mark.parametrize("dialect", NATIVE)
def test_finite_floats_parse_as_the_same_number_in_every_native_dialect(dialect: str) -> None:
    for value in FINITE:
        text = probe(value, "float", dialect).inline(string_quoter(dialect))
        lit = text.removeprefix("SELECT 1 AS x FROM t WHERE s = ")
        tree = sqlglot.parse_one(f"SELECT {lit} AS x", read=SQLGLOT_NAMES[dialect])
        assert _number(tree.expressions[0].this) == value, (dialect, value, lit)


@pytest.mark.parametrize("dialect", NATIVE)
def test_decimals_inline_as_plain_numbers(dialect: str) -> None:
    for value in [Decimal("2.50"), Decimal("1E+3"), Decimal("-1.5E-7"), Decimal("0")]:
        text = probe(value, "decimal", dialect).inline(string_quoter(dialect))
        lit = text.removeprefix("SELECT 1 AS x FROM t WHERE s = ")
        assert "E" not in lit.upper()
        tree = sqlglot.parse_one(f"SELECT {lit} AS x", read=SQLGLOT_NAMES[dialect])
        assert Decimal(str(_number(tree.expressions[0].this))) == value or Decimal(lit) == value
