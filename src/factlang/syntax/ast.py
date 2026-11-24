"""Syntax tree for FactLang documents (spec 4.3-4.5). Plain frozen dataclasses."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from factlang.diagnostics import Span


@dataclass(frozen=True)
class Name:
    """One identifier part; ``text`` is unquoted, ``quoted`` records the source form."""

    text: str
    quoted: bool
    span: Span


@dataclass(frozen=True)
class Ref:
    """A dotted reference of one or more names; table or column is decided later."""

    parts: tuple[Name, ...]
    span: Span


@dataclass(frozen=True)
class Edge:
    """``left -> right``, the operand form of relationship verbs."""

    left: Ref
    right: Ref
    span: Span


class LiteralKind(Enum):
    NUMBER = "NUMBER"
    PERCENT = "PERCENT"
    DURATION = "DURATION"
    STRING = "STRING"
    IDENT = "IDENT"
    QIDENT = "QIDENT"
    CARDINALITY = "CARDINALITY"
    BOOL = "BOOL"
    DATE = "DATE"
    TIMESTAMP = "TIMESTAMP"
    LIST = "LIST"


@dataclass(frozen=True)
class Literal:
    """A typed literal.

    ``value`` is a ``Decimal`` (NUMBER; PERCENT as a ratio), ``int`` seconds (DURATION),
    ``str`` (STRING, IDENT, QIDENT unquoted, CARDINALITY, and the raw ISO text of DATE
    and TIMESTAMP), ``bool`` (BOOL) or ``tuple[Literal, ...]`` (LIST).
    """

    kind: LiteralKind
    value: object
    span: Span


@dataclass(frozen=True)
class Attribute:
    name: str
    op: str | None
    value: Literal
    span: Span


@dataclass(frozen=True)
class Comparison:
    column: Name
    op: str
    value: Literal
    span: Span


@dataclass(frozen=True)
class InList:
    column: Name
    values: tuple[Literal, ...]
    negated: bool
    span: Span


@dataclass(frozen=True)
class IsNull:
    column: Name
    negated: bool
    span: Span


@dataclass(frozen=True)
class Between:
    column: Name
    low: Literal
    high: Literal
    span: Span


@dataclass(frozen=True)
class FilterName:
    """A bare boolean column used as an atom: ``deleted`` (≡ ``deleted = TRUE``)."""

    column: Name
    span: Span


@dataclass(frozen=True)
class And:
    items: tuple[Cond, ...]
    span: Span


@dataclass(frozen=True)
class Or:
    items: tuple[Cond, ...]
    span: Span


@dataclass(frozen=True)
class Not:
    item: Cond
    span: Span


Cond = Comparison | InList | IsNull | Between | FilterName | And | Or | Not


@dataclass(frozen=True)
class Filter:
    condition: Cond
    span: Span


@dataclass(frozen=True)
class Claim:
    verb: str
    verb_span: Span
    operands: tuple[Ref | Edge | Literal, ...]
    attributes: tuple[Attribute, ...]
    filter: Filter | None
    comment: str | None
    line_text: str
    span: Span


@dataclass(frozen=True)
class Directive:
    """``USE catalog.schema``: the default namespace for later lines."""

    namespace: Ref
    comment: str | None
    span: Span


@dataclass(frozen=True)
class Document:
    items: tuple[Claim | Directive, ...]
    source: str
