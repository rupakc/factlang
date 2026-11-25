"""Canonical printer for FactLang (spec 4.6).

``format_claim`` / ``format_document`` print the canonical form of a parsed tree:
single spaces; ``=`` in attributes becomes ``==`` (a filter ``=`` stays); numbers via
``Decimal.normalize()`` without an exponent; a PERCENT literal prints as ``<n>%``;
durations in the largest exact unit (``w d h m s``); strings single-quoted with ``''``;
names double-quoted (inner ``"`` doubled) only if they were quoted in the source;
attributes in source order; the comment appended as ``  # text``; continuation lines
joined (the AST holds logical lines).

Filters are parenthesised only where precedence (``NOT`` > ``AND`` > ``OR``) or a
nested same-operator group in the tree requires it, so the printed text re-parses to
the same tree shape.

Not preserved: blank lines and comment-only lines are not represented in the AST,
so ``format_document`` drops them. This module imports only lower layers (ast,
tokens, diagnostics); it must not import the parser.
"""

from __future__ import annotations

import re
from decimal import Decimal

from factlang.syntax import ast
from factlang.syntax.tokens import FILTER_KEYWORDS

LK = ast.LiteralKind

_UNITS = (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1))


def _shift(value: Decimal, places: int) -> Decimal:
    """``value * 10**places`` by exponent change: exact, never rounded."""
    sign, digits, exp = value.as_tuple()
    assert isinstance(exp, int)
    return Decimal((sign, digits, exp + places))


def format_number(value: Decimal) -> str:
    """Plain-notation text of ``value`` without trailing zeros, exact at any width."""
    if value.is_zero():
        return "0"
    sign, digits, exp = value.as_tuple()
    assert isinstance(exp, int)
    ds = list(digits)
    while len(ds) > 1 and ds[-1] == 0:
        ds.pop()
        exp += 1
    text = "".join(map(str, ds))
    if exp >= 0:
        text += "0" * exp
    elif -exp >= len(text):
        text = "0." + "0" * (-exp - len(text)) + text
    else:
        text = text[:exp] + "." + text[exp:]
    return ("-" if sign else "") + text


def _duration(seconds: int) -> str:
    if seconds == 0:
        return "0s"
    for unit, size in _UNITS:
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"  # unreachable: 's' divides everything


def _string(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _quoted(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


_BARE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_RESERVED_BARE = frozenset({"WHERE", "USE", *FILTER_KEYWORDS})
_UNREPRESENTABLE = {"\r": "a carriage return", "\n": "a newline", "\x00": "a NUL character"}


def needs_quoting(name: str) -> bool:
    """True iff writing ``name`` bare in FactLang source would not denote that exact name.

    A bare name must match ``[A-Za-z_][A-Za-z0-9_$]*`` (ASCII), must not be an uppercase
    word (at least one letter and no lowercase letter: such a word starts the attribute
    section or is read as a keyword) and must not be ``WHERE``, ``USE`` or a member of
    ``FILTER_KEYWORDS`` (case-sensitive). ``deleted``, ``Status`` and ``order`` need no
    quotes; ``ID``, ``NULL``, ``order items`` and ``2fa`` do. Quoting is always safe: when
    in doubt (a non-ASCII letter, say) the answer is ``True``.
    """
    if _BARE_NAME.fullmatch(name) is None:
        return True
    if any(c.isalpha() for c in name) and not any(c.islower() for c in name):
        return True
    return name in _RESERVED_BARE


def quote_name(name: str) -> str:
    """The FactLang double-quoted form of ``name``: ``"`` + ``name`` with ``"`` doubled + ``"``.

    Raises ``ValueError`` for an empty name and for a name holding a carriage return or a
    newline (a quoted identifier cannot span lines) or NUL (no warehouse identifier can
    hold it; rendering would refuse it as FL502).
    """
    if not isinstance(name, str):
        raise TypeError(f"name must be a str, got {type(name).__name__}")
    if not name:
        raise ValueError("an empty name cannot be quoted")
    for char, what in _UNREPRESENTABLE.items():
        if char in name:
            raise ValueError(f"a name holding {what} cannot be written in FactLang source")
    return _quoted(name)


def _name(name: ast.Name) -> str:
    return _quoted(name.text) if name.quoted else name.text


def _ref(ref: ast.Ref) -> str:
    return ".".join(_name(p) for p in ref.parts)


def _literal(lit: ast.Literal) -> str:
    kind, value = lit.kind, lit.value
    if kind is LK.NUMBER:
        assert isinstance(value, Decimal)
        return format_number(value)
    if kind is LK.PERCENT:
        assert isinstance(value, Decimal)
        return format_number(_shift(value, 2)) + "%"
    if kind is LK.DURATION:
        assert isinstance(value, int)
        return _duration(value)
    if kind is LK.STRING:
        return _string(str(value))
    if kind is LK.QIDENT:
        return _quoted(str(value))
    if kind is LK.BOOL:
        return "TRUE" if value else "FALSE"
    if kind is LK.DATE:
        return "DATE " + _string(str(value))
    if kind is LK.TIMESTAMP:
        return "TIMESTAMP " + _string(str(value))
    if kind is LK.LIST:
        assert isinstance(value, tuple)
        return "[" + ", ".join(_literal(v) for v in value) + "]"
    return str(value)  # IDENT, CARDINALITY


def _operand(op: ast.Ref | ast.Edge | ast.Literal) -> str:
    if isinstance(op, ast.Ref):
        return _ref(op)
    if isinstance(op, ast.Edge):
        return f"{_ref(op.left)} -> {_ref(op.right)}"
    return _literal(op)


def _attribute(attr: ast.Attribute) -> str:
    parts = [attr.name]
    if attr.op is not None:
        parts.append("==" if attr.op == "=" else attr.op)
    parts.append(_literal(attr.value))
    return " ".join(parts)


def _cond(cond: ast.Cond, parent: type | None = None) -> str:
    if isinstance(cond, ast.Comparison):
        return f"{_name(cond.column)} {cond.op} {_literal(cond.value)}"
    if isinstance(cond, ast.InList):
        kw = "NOT IN" if cond.negated else "IN"
        return f"{_name(cond.column)} {kw} [{', '.join(_literal(v) for v in cond.values)}]"
    if isinstance(cond, ast.IsNull):
        return f"{_name(cond.column)} IS {'NOT ' if cond.negated else ''}NULL"
    if isinstance(cond, ast.Between):
        return f"{_name(cond.column)} BETWEEN {_literal(cond.low)} AND {_literal(cond.high)}"
    if isinstance(cond, ast.FilterName):
        return _name(cond.column)
    if isinstance(cond, ast.Not):
        inner = _cond(cond.item, ast.Not)
        return f"NOT {inner}"
    word = " AND " if isinstance(cond, ast.And) else " OR "
    text = word.join(_cond(item, type(cond)) for item in cond.items)
    # A group needs parentheses under NOT, under the same operator (it was explicitly
    # grouped), and an OR under AND. An AND under OR binds tighter and needs none.
    needs = (
        parent is ast.Not or parent is type(cond) or (parent is ast.And and type(cond) is ast.Or)
    )
    return f"({text})" if needs else text


def format_claim(claim: ast.Claim) -> str:
    """The canonical one-line form of a claim, comment included."""
    parts = [claim.verb]
    parts.extend(_operand(o) for o in claim.operands)
    parts.extend(_attribute(a) for a in claim.attributes)
    if claim.filter is not None:
        parts.append("WHERE " + _cond(claim.filter.condition))
    text = " ".join(parts)
    return _with_comment(text, claim.comment)


def _with_comment(text: str, comment: str | None) -> str:
    if comment is None:
        return text
    return f"{text}  # {comment}" if comment else f"{text}  #"


def _directive(d: ast.Directive) -> str:
    return _with_comment("USE " + _ref(d.namespace), d.comment)


def format_document(doc: ast.Document) -> str:
    """Canonical text of a document: one item per line, each ending in a newline."""
    lines = [
        _directive(item) if isinstance(item, ast.Directive) else format_claim(item)
        for item in doc.items
    ]
    return "".join(line + "\n" for line in lines)


__all__ = ["format_claim", "format_document", "needs_quoting", "quote_name"]
