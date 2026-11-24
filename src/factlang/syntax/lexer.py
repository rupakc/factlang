"""Hand-written, never-raising scanner for FactLang (spec 4.2)."""

from __future__ import annotations

import decimal
from decimal import Decimal

from factlang.diagnostics import DiagnosticBag, Span
from factlang.syntax.tokens import Token, TokenKind

_DIGITS = "0123456789"
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_CARDINALITIES = ("1:1", "1:N", "N:1", "N:M")
_TWO_CHAR_OPS = (">=", "<=", "==", "!=")
_ONE_CHAR_OPS = "=<>"
_PUNCT = {
    ".": TokenKind.DOT,
    ",": TokenKind.COMMA,
    "[": TokenKind.LBRACKET,
    "]": TokenKind.RBRACKET,
    "(": TokenKind.LPAREN,
    ")": TokenKind.RPAREN,
}


def _ident_start(c: str) -> bool:
    return c == "_" or c.isalpha()


def _ident_part(c: str) -> bool:
    return c in "_$" or c.isalpha() or c in _DIGITS


def _percent(num: Decimal) -> Decimal:
    """``num / 100`` exactly, by lowering the exponent (no rounding at any size)."""
    sign, digits, exp = num.as_tuple()
    assert isinstance(exp, int)  # a lexed number is always finite
    return Decimal((sign, digits, exp - 2))


def tokenize(source: str, bag: DiagnosticBag) -> list[Token]:
    """Tokenize ``source``; never raises, always ends with an EOF token."""
    if source.startswith("\ufeff"):
        source = source[1:]
    src = source.replace("\r\n", "\n").replace("\r", "\n")
    n = len(src)
    tokens: list[Token] = []
    i = 0
    line = 1
    line_start = 0

    def emit(kind: TokenKind, start: int, end: int, value: object | None = None) -> None:
        text = src[start:end]
        span = Span(line, start - line_start + 1, end - line_start + 1)
        tokens.append(Token(kind, text, text if value is None else value, span))

    def error(start: int, end: int, code: str, message: str, hint: str | None = None) -> None:
        span = Span(line, start - line_start + 1, end - line_start + 1)
        bag.error(code, message, span, hint=hint)
        emit(TokenKind.ERROR, start, end)

    def scan_number(j: int) -> int:
        """End index of a NUMBER whose digits start at ``j``."""
        while j < n and (
            src[j] in _DIGITS or (src[j] == "_" and j + 1 < n and src[j + 1] in _DIGITS)
        ):
            j += 1
        if j + 1 < n and src[j] == "." and src[j + 1] in _DIGITS:
            j += 1
            while j < n and src[j] in _DIGITS:
                j += 1
        if j < n and src[j] in "eE":
            k = j + 1
            if k < n and src[k] in "+-":
                k += 1
            if k < n and src[k] in _DIGITS:
                while k < n and src[k] in _DIGITS:
                    k += 1
                j = k
        return j

    def quoted(start: int, quote: str, kind: TokenKind) -> int:
        j = start + 1
        chars: list[str] = []
        while j < n and src[j] != "\n":
            if src[j] == quote:
                if j + 1 < n and src[j + 1] == quote:
                    chars.append(quote)
                    j += 2
                    continue
                emit(kind, start, j + 1, "".join(chars))
                return j + 1
            chars.append(src[j])
            j += 1
        what = "string" if kind is TokenKind.STRING else "quoted identifier"
        error(start, j, "FL101", f"unterminated {what}", f"close it with {quote}")
        return j

    while i < n:
        c = src[i]
        if c in " \t":
            i += 1
        elif c == "\n":
            emit(TokenKind.NEWLINE, i, i + 1)
            i += 1
            line += 1
            line_start = i
        elif c == "\\":
            j = i + 1
            while j < n and src[j] in " \t":
                j += 1
            if j >= n:
                span = Span(line, i - line_start + 1, i - line_start + 2)
                bag.error("FL205", "dangling line continuation", span, hint="remove the `\\`")
                i = j
            elif src[j] == "\n":
                i = j + 1
                line += 1
                line_start = i
            else:
                error(i, i + 1, "FL102", "unexpected character '\\'")
                i += 1
        elif c == "#":
            j = i
            while j < n and src[j] != "\n":
                j += 1
            emit(TokenKind.COMMENT, i, j)
            i = j
        elif c == "'":
            i = quoted(i, "'", TokenKind.STRING)
        elif c in '"`':
            i = quoted(i, c, TokenKind.QIDENT)
        elif any(
            src.startswith(card, i) and not (i + 3 < n and _ident_part(src[i + 3]))
            for card in _CARDINALITIES
        ):
            emit(TokenKind.CARDINALITY, i, i + 3)
            i += 3
        elif c in _DIGITS or (c == "-" and i + 1 < n and src[i + 1] in _DIGITS):
            neg = c == "-"
            d = i + 1 if neg else i
            j = d
            while j < n and src[j] in _DIGITS:
                j += 1
            # DURATION: plain digit run, one unit letter, then a non-identifier char.
            if (
                not neg
                and j < n
                and src[j] in _UNITS
                and not (j + 1 < n and _ident_part(src[j + 1]))
            ):
                try:
                    emit(TokenKind.DURATION, i, j + 1, int(src[i:j]) * _UNITS[src[j]])
                except ValueError:
                    error(i, j + 1, "FL102", "number too large")
                i = j + 1
                continue
            end = scan_number(d)
            clean = src[i:end].replace("_", "")
            try:
                num = Decimal(clean)
            except decimal.InvalidOperation:
                error(i, end, "FL102", "invalid number")
                i = end
                continue
            if abs(num.adjusted()) > 1000:
                error(
                    i,
                    end + (1 if end < n and src[end] == "%" else 0),
                    "FL102",
                    "number out of range",
                )
                i = end + (1 if end < n and src[end] == "%" else 0)
                continue
            if end < n and src[end] == "%":
                emit(TokenKind.PERCENT, i, end + 1, _percent(num))
                i = end + 1
            else:
                emit(TokenKind.NUMBER, i, end, num)
                i = end
        elif c == "-" and src.startswith("->", i):
            emit(TokenKind.ARROW, i, i + 2)
            i += 2
        elif src[i : i + 2] in _TWO_CHAR_OPS:
            emit(TokenKind.OP, i, i + 2)
            i += 2
        elif c in _ONE_CHAR_OPS:
            emit(TokenKind.OP, i, i + 1)
            i += 1
        elif c in _PUNCT:
            emit(_PUNCT[c], i, i + 1)
            i += 1
        elif _ident_start(c):
            j = i + 1
            while j < n and _ident_part(src[j]):
                j += 1
            emit(TokenKind.IDENT, i, j)
            i = j
        else:
            error(i, i + 1, "FL102", f"unexpected character {c!r}")
            i += 1

    eof = Span(line, n - line_start + 1, n - line_start + 1)
    tokens.append(Token(TokenKind.EOF, "", "", eof))
    return tokens
