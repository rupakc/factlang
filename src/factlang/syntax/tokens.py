"""Token kinds and the token record (spec 4.2)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from factlang.diagnostics import Span


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
