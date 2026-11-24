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

import decimal
from decimal import Decimal

from factlang.syntax import ast

LK = ast.LiteralKind

_WIDE = decimal.Context(prec=60, Emax=decimal.MAX_EMAX, Emin=decimal.MIN_EMIN, traps=[])
_UNITS = (("w", 604800), ("d", 86400), ("h", 3600), ("m", 60), ("s", 1))


def _number(value: Decimal) -> str:
    return format(value.normalize(_WIDE), "f")


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


def _name(name: ast.Name) -> str:
    return _quoted(name.text) if name.quoted else name.text


def _ref(ref: ast.Ref) -> str:
    return ".".join(_name(p) for p in ref.parts)


def _literal(lit: ast.Literal) -> str:
    kind, value = lit.kind, lit.value
    if kind is LK.NUMBER:
        assert isinstance(value, Decimal)
        return _number(value)
    if kind is LK.PERCENT:
        assert isinstance(value, Decimal)
        return _number(value.scaleb(2, context=_WIDE)) + "%"
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


__all__ = ["format_claim", "format_document"]
