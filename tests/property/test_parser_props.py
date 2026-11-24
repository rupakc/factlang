from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from decimal import Decimal
from enum import Enum

from hypothesis import given, settings
from hypothesis import strategies as st

from factlang.diagnostics import Span
from factlang.syntax import ast, parse
from factlang.syntax.formatter import format_claim, format_document

LK = ast.LiteralKind
SP = Span(1, 1, 1)

# -- strategies over the grammar (valid claims only) -----------------------------------
_text = st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")), max_size=8)
_ident = st.from_regex(r"[a-z_][a-z0-9_$]{0,6}", fullmatch=True)
_quoted = st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=1, max_size=8)


def _name(text: str, quoted: bool) -> ast.Name:
    return ast.Name(text, quoted, SP)


names = st.one_of(_ident.map(lambda t: _name(t, False)), _quoted.map(lambda t: _name(t, True)))
refs = st.lists(names, min_size=1, max_size=4).map(lambda p: ast.Ref(tuple(p), SP))


def _lit(kind: ast.LiteralKind, value: object) -> ast.Literal:
    return ast.Literal(kind, value, SP)


def _decimals(negative: bool) -> st.SearchStrategy[Decimal]:
    lo = -(10**9) if negative else 0
    return st.builds(lambda n, s: Decimal(n).scaleb(-s), st.integers(lo, 10**9), st.integers(0, 6))


# The lexer reads '-' as a sign only after a non-operand token, so a negative number
# may follow an operator, '[' or ',' but not an identifier (or BETWEEN / AND).
nonneg_numbers = _decimals(False).map(lambda d: _lit(LK.NUMBER, d))
any_numbers = _decimals(True).map(lambda d: _lit(LK.NUMBER, d))
percents = _decimals(True).map(lambda d: _lit(LK.PERCENT, d.scaleb(-2)))
nonneg_percents = _decimals(False).map(lambda d: _lit(LK.PERCENT, d.scaleb(-2)))
durations = st.integers(0, 10**7).map(lambda s: _lit(LK.DURATION, s))
strings = _text.map(lambda s: _lit(LK.STRING, s))
idents = _ident.map(lambda s: _lit(LK.IDENT, s))
qidents = _quoted.map(lambda s: _lit(LK.QIDENT, s))
cards = st.sampled_from(["1:1", "1:N", "N:1", "N:M"]).map(lambda s: _lit(LK.CARDINALITY, s))
dates = _text.map(lambda s: _lit(LK.DATE, s))
timestamps = _text.map(lambda s: _lit(LK.TIMESTAMP, s))

_list_scalars = st.one_of(
    any_numbers, percents, durations, strings, idents, qidents, cards, dates, timestamps
)
lists = st.lists(_list_scalars, max_size=4).map(lambda xs: _lit(LK.LIST, tuple(xs)))
_attr_scalars = st.one_of(
    nonneg_numbers, nonneg_percents, durations, strings, idents, qidents, cards, dates, timestamps
)

operands = st.one_of(
    refs,
    st.tuples(refs, refs).map(lambda t: ast.Edge(t[0], t[1], SP)),
    st.one_of(nonneg_numbers, nonneg_percents, durations, strings),
)

_ATTR_NAMES = ["MIN", "MAX", "VALUE", "WITH", "BY", "IN", "IS", "NOT", "BETWEEN", "CARD", "EQ"]
_OPS = [">=", "<=", "==", "!=", "=", "<", ">"]


@st.composite
def attributes(draw: st.DrawFn, name: str) -> ast.Attribute:
    if draw(st.booleans()):
        op = draw(st.sampled_from(_OPS))
        value = draw(st.one_of(_attr_scalars, any_numbers, lists))
    else:
        op = None
        value = draw(st.one_of(_attr_scalars, lists))
    return ast.Attribute(name, op, value, SP)


_cols = names
_filter_scalars = st.one_of(
    any_numbers,
    strings,
    st.booleans().map(lambda b: _lit(LK.BOOL, b)),
    dates,
    timestamps,
)
_between_scalars = st.one_of(
    nonneg_numbers,
    strings,
    st.booleans().map(lambda b: _lit(LK.BOOL, b)),
    dates,
    timestamps,
)
leaves = st.one_of(
    st.builds(ast.Comparison, _cols, st.sampled_from(_OPS), _filter_scalars, st.just(SP)),
    st.builds(
        ast.InList,
        _cols,
        st.lists(_filter_scalars, max_size=3).map(tuple),
        st.booleans(),
        st.just(SP),
    ),
    st.builds(ast.IsNull, _cols, st.booleans(), st.just(SP)),
    st.builds(ast.Between, _cols, _between_scalars, _between_scalars, st.just(SP)),
)
conditions: st.SearchStrategy[ast.Cond] = st.recursive(
    leaves,
    lambda inner: st.one_of(
        st.lists(inner, min_size=2, max_size=3).map(lambda xs: ast.And(tuple(xs), SP)),
        st.lists(inner, min_size=2, max_size=3).map(lambda xs: ast.Or(tuple(xs), SP)),
        inner.map(lambda c: ast.Not(c, SP)),
    ),
    max_leaves=8,  # nesting stays far below the FL207 limit of 64
)


@st.composite
def claims(draw: st.DrawFn) -> ast.Claim:
    verb = draw(
        st.sampled_from(["ROWS", "UNIQUE", "NULLS", "JOIN", "RANGE", "FRESH", "VALUES", "NOTE"])
    )
    ops = tuple(draw(st.lists(operands, max_size=3)))
    attr_names = draw(st.lists(st.sampled_from(_ATTR_NAMES), unique=True, max_size=4))
    attrs = tuple(draw(attributes(n)) for n in attr_names)
    flt = ast.Filter(draw(conditions), SP) if draw(st.booleans()) else None
    comment = draw(st.one_of(st.none(), _text.map(str.strip)))
    return ast.Claim(verb, SP, ops, attrs, flt, comment, "", SP)


def _parse_one(src: str) -> ast.Claim:
    res = parse(src)
    assert not res.diagnostics.has_errors, res.diagnostics.render(src)
    (item,) = res.document.items
    assert isinstance(item, ast.Claim)
    return item


def _shape(node: object) -> object:
    """The tree without spans/line text, so two trees compare by meaning of structure."""
    if isinstance(node, tuple):
        return tuple(_shape(x) for x in node)
    if isinstance(node, Enum):
        return node.value
    if is_dataclass(node) and not isinstance(node, type):
        skip = {"span", "verb_span", "line_text"}
        return (
            type(node).__name__,
            *(_shape(getattr(node, f.name)) for f in fields(node) if f.name not in skip),
        )
    return node


def _canonical(claim: ast.Claim) -> ast.Claim:
    attrs = tuple(replace(a, op="==" if a.op == "=" else a.op) for a in claim.attributes)
    return replace(claim, attributes=attrs)


@settings(max_examples=300, deadline=None)
@given(claims())
def test_parse_preserves_structure(claim: ast.Claim) -> None:
    parsed = _parse_one(format_claim(claim))
    assert _shape(parsed) == _shape(_canonical(claim))


@settings(max_examples=300, deadline=None)
@given(claims())
def test_parse_format_parse_fixpoint(claim: ast.Claim) -> None:
    once = format_claim(claim)
    twice = format_claim(_parse_one(once))
    assert twice == once


@settings(max_examples=500, deadline=None)
@given(st.text())
def test_parse_never_raises(source: str) -> None:
    parse(source)


@settings(max_examples=200, deadline=None)
@given(st.lists(claims(), max_size=4))
def test_crlf_equivalent_to_lf(cs: list[ast.Claim]) -> None:
    lf = "\n".join(format_claim(c) for c in cs) + "\n"
    crlf = lf.replace("\n", "\r\n")
    a, b = parse(lf), parse(crlf)
    assert format_document(a.document) == format_document(b.document)
    assert a.diagnostics.codes() == b.diagnostics.codes()


@settings(max_examples=300, deadline=None)
@given(st.text(alphabet="ROWSUNIQE tabc.,'\"[]()=<>!-%#\\0123456789:\n ", max_size=60))
def test_crlf_equivalent_to_lf_on_arbitrary_text(source: str) -> None:
    a, b = parse(source), parse(source.replace("\n", "\r\n"))
    assert format_document(a.document) == format_document(b.document)
    assert a.diagnostics.codes() == b.diagnostics.codes()
