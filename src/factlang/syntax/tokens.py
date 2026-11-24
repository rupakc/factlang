"""Token kinds and the token record (spec 4.2)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from factlang.diagnostics import Span

FILTER_KEYWORDS: frozenset[str] = frozenset(
    {"AND", "OR", "NOT", "IN", "IS", "NULL", "BETWEEN", "TRUE", "FALSE", "DATE", "TIMESTAMP"}
)
"""The words reserved inside a ``WHERE`` filter (spec 4.5): a bare column name may not be one
of them (FL206; quote it instead). This is the filter-keyword set only, not every word the
language treats specially (verbs and clause keywords are not in it)."""


class TokenKind(Enum):
    IDENT = "IDENT"
    QIDENT = "QIDENT"
    NUMBER = "NUMBER"
    PERCENT = "PERCENT"
    DURATION = "DURATION"
    CARDINALITY = "CARDINALITY"
    STRING = "STRING"
    ARROW = "ARROW"
    OP = "OP"
    DOT = "DOT"
    COMMA = "COMMA"
    LBRACKET = "LBRACKET"
    RBRACKET = "RBRACKET"
    LPAREN = "LPAREN"
    RPAREN = "RPAREN"
    COMMENT = "COMMENT"
    NEWLINE = "NEWLINE"
    ERROR = "ERROR"
    EOF = "EOF"


@dataclass(frozen=True)
class Token:
    kind: TokenKind
    text: str
    value: object
    span: Span
