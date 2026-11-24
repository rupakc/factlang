"""Recursive-descent parser: one claim per line, recovery per line (spec 4.3-4.5)."""

from __future__ import annotations

from dataclasses import dataclass

from factlang.diagnostics import DiagnosticBag, Span
from factlang.syntax import ast
from factlang.syntax.lexer import tokenize
from factlang.syntax.tokens import Token, TokenKind

K = TokenKind
LK = ast.LiteralKind

MAX_NESTING = 64

_FILTER_KEYWORDS = frozenset(
    {"AND", "OR", "NOT", "IN", "IS", "NULL", "BETWEEN", "TRUE", "FALSE", "DATE", "TIMESTAMP"}
)
_SCALAR_VALUE_KINDS = {
    K.NUMBER: LK.NUMBER,
    K.PERCENT: LK.PERCENT,
    K.DURATION: LK.DURATION,
    K.STRING: LK.STRING,
    K.IDENT: LK.IDENT,
    K.QIDENT: LK.QIDENT,
    K.CARDINALITY: LK.CARDINALITY,
}
_OPERAND_LITERAL_KINDS = {
    K.NUMBER: LK.NUMBER,
    K.PERCENT: LK.PERCENT,
    K.DURATION: LK.DURATION,
    K.STRING: LK.STRING,
}


@dataclass(frozen=True)
class ParseResult:
    document: ast.Document
    diagnostics: DiagnosticBag


class _LineError(Exception):
    """Raised after a diagnostic is recorded; the line is dropped."""


def is_uppercase_ident(token: Token) -> bool:
    """A bare identifier with at least one letter and no lowercase letter."""
    text = token.text
    return (
        token.kind is K.IDENT
        and any(c.isalpha() for c in text)
        and not any(c.islower() for c in text)
    )


def _join(a: Span, b: Span) -> Span:
    if a.line == b.line and b.end_col >= a.col:
        return Span(a.line, a.col, b.end_col)
    return a


def _span_of(node: object) -> Span:
    span = getattr(node, "span", None)
    assert isinstance(span, Span)
    return span


class _Line:
    """Parser state for the tokens of one logical line (no NEWLINE/COMMENT/EOF)."""

    def __init__(self, toks: list[Token], bag: DiagnosticBag) -> None:
        self.toks = toks
        self.pos = 0
        self.bag = bag
        self.depth = 0

    def enter(self, span: Span) -> None:
        """Count one level of parens / NOT / list nesting; refuse past ``MAX_NESTING``."""
        self.depth += 1
        if self.depth > MAX_NESTING:
            raise self.fail(
                "FL207",
                "nesting too deep",
                span,
                hint=f"parentheses, NOT and lists may nest at most {MAX_NESTING} levels",
            )

    # -- primitives -------------------------------------------------------
    def peek(self, offset: int = 0) -> Token | None:
        i = self.pos + offset
        return self.toks[i] if i < len(self.toks) else None

    def next(self) -> Token:
        tok = self.toks[self.pos]
        self.pos += 1
        return tok

    def at_end(self) -> bool:
        return self.pos >= len(self.toks)

    def kw(self, word: str, offset: int = 0) -> bool:
        tok = self.peek(offset)
        return tok is not None and tok.kind is K.IDENT and tok.text == word

    def end_span(self) -> Span:
        last = self.toks[-1].span
        return Span(last.line, last.end_col, last.end_col)

    def here(self) -> Span:
        tok = self.peek()
        return tok.span if tok is not None else self.end_span()

    def fail(self, code: str, message: str, span: Span, hint: str | None = None) -> _LineError:
        self.bag.error(code, message, span, hint=hint)
        return _LineError()

    # -- names, refs, values ----------------------------------------------
    @staticmethod
    def name_of(tok: Token) -> ast.Name:
        if tok.kind is K.QIDENT:
            return ast.Name(str(tok.value), True, tok.span)
        return ast.Name(tok.text, False, tok.span)

    def ref(self, what: str = "a reference") -> ast.Ref:
        tok = self.peek()
        if tok is None or tok.kind not in (K.IDENT, K.QIDENT):
            raise self.fail("FL203", f"expected {what}", self.here())
        parts = [self.name_of(self.next())]
        while (nxt := self.peek()) is not None and nxt.kind is K.DOT:
            self.next()
            part = self.peek()
            if part is None or part.kind not in (K.IDENT, K.QIDENT):
                raise self.fail(
                    "FL203",
                    "expected a name after '.'",
                    self.here(),
                    hint="a reference part is an identifier or a quoted identifier",
                )
            parts.append(self.name_of(self.next()))
        return ast.Ref(tuple(parts), _join(parts[0].span, parts[-1].span))

    def typed_literal(self, tok: Token, code: str) -> ast.Literal:
        """``DATE '...'`` / ``TIMESTAMP '...'``; ``tok`` is the keyword, already consumed."""
        s = self.peek()
        if s is None or s.kind is not K.STRING:
            raise self.fail(code, f"expected an ISO string after {tok.text}", self.here())
        self.next()
        kind = LK.DATE if tok.text == "DATE" else LK.TIMESTAMP
        return ast.Literal(kind, str(s.value), _join(tok.span, s.span))

    def value(self) -> ast.Literal:
        tok = self.peek()
        if tok is None:
            raise self.fail("FL201", "expected a value", self.end_span())
        if tok.kind is K.LBRACKET:
            return self.list_value()
        if tok.kind is K.IDENT and tok.text in ("DATE", "TIMESTAMP"):
            nxt = self.peek(1)
            if nxt is not None and nxt.kind is K.STRING:
                self.next()
                return self.typed_literal(tok, "FL201")
        kind = _SCALAR_VALUE_KINDS.get(tok.kind)
        if kind is None:
            raise self.fail("FL201", "expected a value", tok.span)
        self.next()
        return ast.Literal(kind, tok.value, tok.span)

    def list_value(self) -> ast.Literal:
        open_tok = self.next()
        self.enter(open_tok.span)
        items: list[ast.Literal] = []
        while True:
            tok = self.peek()
            if tok is None:
                raise self.fail("FL202", "unclosed list", open_tok.span, hint="add a closing ']'")
            if tok.kind is K.RBRACKET:
                self.next()
                self.depth -= 1
                return ast.Literal(LK.LIST, tuple(items), _join(open_tok.span, tok.span))
            items.append(self.value())
            sep = self.peek()
            if sep is None:
                raise self.fail("FL202", "unclosed list", open_tok.span, hint="add a closing ']'")
            if sep.kind is K.COMMA:
                self.next()
            elif sep.kind is not K.RBRACKET:
                raise self.fail("FL203", "expected ',' or ']' in list", sep.span)

    # -- claim ------------------------------------------------------------
    def directive(self, comment: str | None) -> ast.Directive:
        use = self.next()
        ns = self.ref("a namespace after USE")
        if not self.at_end():
            raise self.fail(
                "FL203",
                "unexpected token after USE directive",
                self.here(),
                hint="USE must be alone on its line",
            )
        return ast.Directive(ns, comment, _join(use.span, ns.span))

    def claim(self, comment: str | None, line_text: str) -> ast.Claim:
        first = self.toks[0]
        if not is_uppercase_ident(first):
            raise self.fail(
                "FL203",
                "expected a verb (an all-uppercase identifier)",
                first.span,
                hint="a claim starts with a verb such as ROWS or UNIQUE",
            )
        self.next()
        operands: list[ast.Ref | ast.Edge | ast.Literal] = []
        while (tok := self.peek()) is not None:
            if tok.kind in (K.IDENT, K.QIDENT) and not is_uppercase_ident(tok):
                left = self.ref()
                if (arrow := self.peek()) is not None and arrow.kind is K.ARROW:
                    self.next()
                    right = self.ref("a reference after '->'")
                    operands.append(ast.Edge(left, right, _join(left.span, right.span)))
                else:
                    operands.append(left)
            elif tok.kind in _OPERAND_LITERAL_KINDS:
                self.next()
                operands.append(ast.Literal(_OPERAND_LITERAL_KINDS[tok.kind], tok.value, tok.span))
            elif is_uppercase_ident(tok):
                break
            else:
                raise self.fail("FL203", f"unexpected {tok.kind.value} {tok.text!r}", tok.span)

        attributes: list[ast.Attribute] = []
        seen: set[str] = set()
        flt: ast.Filter | None = None
        while (tok := self.peek()) is not None:
            if tok.kind is K.IDENT and tok.text == "WHERE":
                flt = self.filter()
                break
            if not is_uppercase_ident(tok):
                raise self.fail(
                    "FL203",
                    f"unexpected {tok.kind.value} {tok.text!r}",
                    tok.span,
                    hint="operands must come before attributes; attribute names are UPPERCASE",
                )
            attributes.append(self.attribute(seen))
        last = self.toks[-1].span
        span = _join(first.span, last)
        return ast.Claim(
            first.text,
            first.span,
            tuple(operands),
            tuple(attributes),
            flt,
            comment,
            line_text,
            span,
        )

    def attribute(self, seen: set[str]) -> ast.Attribute:
        name_tok = self.next()
        if name_tok.text in seen:
            raise self.fail("FL204", f"duplicate attribute {name_tok.text}", name_tok.span)
        seen.add(name_tok.text)
        op: str | None = None
        nxt = self.peek()
        if nxt is not None and nxt.kind is K.OP:
            op = self.next().text
        nxt = self.peek()
        if nxt is not None and nxt.kind is K.IDENT and nxt.text == "WHERE":
            raise self.fail("FL201", f"expected a value for {name_tok.text}", nxt.span)
        if nxt is None:
            raise self.fail("FL201", f"expected a value for {name_tok.text}", self.end_span())
        val = self.value()
        return ast.Attribute(name_tok.text, op, val, _join(name_tok.span, _span_of(val)))

    # -- filter (spec 4.5) --------------------------------------------------
    def filter(self) -> ast.Filter:
        where = self.next()
        if self.at_end():
            raise self.fail("FL206", "expected a condition after WHERE", self.end_span())
        cond = self.condition()
        if not self.at_end():
            tok = self.toks[self.pos]
            raise self.fail(
                "FL206",
                f"unexpected {tok.text!r} in filter",
                tok.span,
                hint="a filter is the last element of a claim and runs to the end of the line",
            )
        return ast.Filter(cond, _join(where.span, self.toks[-1].span))

    def condition(self) -> ast.Cond:
        items = [self.conj()]
        while self.kw("OR"):
            self.next()
            items.append(self.conj())
        if len(items) == 1:
            return items[0]
        return ast.Or(tuple(items), _join(_span_of(items[0]), _span_of(items[-1])))

    def conj(self) -> ast.Cond:
        items = [self.atom()]
        while self.kw("AND"):
            self.next()
            items.append(self.atom())
        if len(items) == 1:
            return items[0]
        return ast.And(tuple(items), _join(_span_of(items[0]), _span_of(items[-1])))

    def atom(self) -> ast.Cond:
        tok = self.peek()
        if tok is None:
            raise self.fail("FL206", "expected a condition", self.end_span())
        if self.kw("NOT"):
            self.next()
            self.enter(tok.span)
            item = self.atom()
            self.depth -= 1
            return ast.Not(item, _join(tok.span, _span_of(item)))
        if tok.kind is K.LPAREN:
            self.next()
            self.enter(tok.span)
            inner = self.condition()
            self.depth -= 1
            close = self.peek()
            if close is None or close.kind is not K.RPAREN:
                raise self.fail("FL206", "expected ')'", self.here())
            self.next()
            return inner
        return self.comparison()

    def column(self) -> ast.Name:
        tok = self.peek()
        if tok is None:
            raise self.fail("FL206", "expected a column name", self.end_span())
        if tok.kind is K.IDENT and tok.text in _FILTER_KEYWORDS:
            raise self.fail(
                "FL206",
                f"{tok.text} is a filter keyword and cannot be a column name",
                tok.span,
                hint='quote the column name, e.g. "' + tok.text + '"',
            )
        if tok.kind not in (K.IDENT, K.QIDENT):
            raise self.fail("FL206", "expected a column name", tok.span)
        self.next()
        nxt = self.peek()
        if nxt is not None and nxt.kind is K.DOT:
            raise self.fail(
                "FL206",
                "a filter column is a single name, not a dotted reference",
                nxt.span,
                hint="filters apply to the claim's own table; drop the table prefix",
            )
        return self.name_of(tok)

    def filter_literal(self) -> ast.Literal:
        tok = self.peek()
        if tok is None:
            raise self.fail("FL206", "expected a literal", self.end_span())
        if tok.kind is K.NUMBER:
            self.next()
            return ast.Literal(LK.NUMBER, tok.value, tok.span)
        if tok.kind is K.STRING:
            self.next()
            return ast.Literal(LK.STRING, tok.value, tok.span)
        if tok.kind is K.IDENT and tok.text in ("TRUE", "FALSE"):
            self.next()
            return ast.Literal(LK.BOOL, tok.text == "TRUE", tok.span)
        if tok.kind is K.IDENT and tok.text in ("DATE", "TIMESTAMP"):
            self.next()
            return self.typed_literal(tok, "FL206")
        if tok.kind is K.IDENT and tok.text == "NULL":
            raise self.fail(
                "FL206", "NULL is not a comparable value", tok.span, hint="use `IS NULL`"
            )
        raise self.fail("FL206", f"expected a literal, found {tok.text!r}", tok.span)

    def filter_list(self) -> tuple[ast.Literal, ...]:
        open_tok = self.peek()
        if open_tok is None or open_tok.kind is not K.LBRACKET:
            raise self.fail("FL206", "expected a list like [1, 2]", self.here())
        self.next()
        items: list[ast.Literal] = []
        while True:
            tok = self.peek()
            if tok is None:
                raise self.fail("FL202", "unclosed list", open_tok.span, hint="add a closing ']'")
            if tok.kind is K.RBRACKET:
                self.next()
                return tuple(items)
            items.append(self.filter_literal())
            sep = self.peek()
            if sep is None:
                raise self.fail("FL202", "unclosed list", open_tok.span, hint="add a closing ']'")
            if sep.kind is K.COMMA:
                self.next()
            elif sep.kind is not K.RBRACKET:
                raise self.fail("FL206", "expected ',' or ']' in list", sep.span)

    def comparison(self) -> ast.Cond:
        col = self.column()
        tok = self.peek()
        if tok is None:
            return ast.FilterName(col, col.span)
        if tok.kind is K.RPAREN or self.kw("AND") or self.kw("OR"):
            return ast.FilterName(col, col.span)
        if tok.kind is K.OP:
            self.next()
            lit = self.filter_literal()
            return ast.Comparison(col, tok.text, lit, _join(col.span, lit.span))
        if self.kw("IN"):
            self.next()
            values = self.filter_list()
            return ast.InList(col, values, False, _join(col.span, self.toks[self.pos - 1].span))
        if self.kw("NOT") and self.kw("IN", 1):
            self.next()
            self.next()
            values = self.filter_list()
            return ast.InList(col, values, True, _join(col.span, self.toks[self.pos - 1].span))
        if self.kw("IS"):
            self.next()
            negated = False
            if self.kw("NOT"):
                self.next()
                negated = True
            if not self.kw("NULL"):
                raise self.fail("FL206", "expected NULL after IS", self.here())
            end = self.next()
            return ast.IsNull(col, negated, _join(col.span, end.span))
        if self.kw("BETWEEN"):
            self.next()
            low = self.filter_literal()
            if not self.kw("AND"):
                raise self.fail("FL206", "expected AND in BETWEEN", self.here())
            self.next()
            high = self.filter_literal()
            return ast.Between(col, low, high, _join(col.span, high.span))
        raise self.fail("FL206", f"expected an operator, found {tok.text!r}", tok.span)


def _fallback_line_text(toks: list[Token]) -> str:
    return " ".join(t.text for t in toks)


def parse_tokens(tokens: list[Token], bag: DiagnosticBag, source: str = "") -> ast.Document:
    """Parse a token stream. A bad line is reported and skipped; the rest still parse."""
    src = source
    if src.startswith("\ufeff"):
        src = src[1:]
    src = src.replace("\r\n", "\n").replace("\r", "\n")
    src_lines = src.split("\n")

    items: list[ast.Claim | ast.Directive] = []
    line: list[Token] = []

    def flush() -> None:
        if not line:
            return
        toks = [t for t in line if t.kind is not K.COMMENT]
        comment: str | None = None
        if line[-1].kind is K.COMMENT:
            comment = line[-1].text[1:].strip()
        if not toks:
            line.clear()
            return
        if any(t.kind is K.ERROR for t in toks):
            line.clear()  # the lexer already reported it
            return
        first, last = toks[0].span.line, toks[-1].span.line
        if src and last <= len(src_lines):
            text = "\n".join(src_lines[first - 1 : last])
        else:
            text = _fallback_line_text(toks)
        state = _Line(toks, bag)
        try:
            if toks[0].kind is K.IDENT and toks[0].text == "USE":
                items.append(state.directive(comment))
            else:
                items.append(state.claim(comment, text))
        except _LineError:
            pass
        line.clear()

    for tok in tokens:
        if tok.kind in (K.NEWLINE, K.EOF):
            flush()
        else:
            line.append(tok)
    flush()
    return ast.Document(tuple(items), source)


def parse(source: str) -> ParseResult:
    """Tokenize and parse ``source``; never raises."""
    bag = DiagnosticBag()
    tokens = tokenize(source, bag)
    doc = parse_tokens(tokens, bag, source)
    return ParseResult(doc, bag)


def parse_condition(source: str) -> tuple[ast.Cond | None, DiagnosticBag]:
    """Parse a bare filter condition (spec 4.5), with or without a leading ``WHERE``.

    The condition is one logical line (comments allowed). Never raises: on any error the
    condition is ``None`` and the reasons are in the bag (``FL206`` for a malformed filter).
    """
    bag = DiagnosticBag()
    tokens = tokenize(source, bag)
    if any(t.kind is K.ERROR for t in tokens):
        return None, bag  # the lexer already reported it
    toks = [t for t in tokens if t.kind not in (K.COMMENT, K.EOF)]
    while toks and toks[0].kind is K.NEWLINE:
        toks.pop(0)
    while toks and toks[-1].kind is K.NEWLINE:
        toks.pop()
    if toks and toks[0].kind is K.IDENT and toks[0].text == "WHERE":
        where = toks.pop(0)
        if not toks:
            bag.error("FL206", "expected a condition after WHERE", where.span)
            return None, bag
    if not toks:
        bag.error("FL206", "expected a condition", None)
        return None, bag
    newline = next((t for t in toks if t.kind is K.NEWLINE), None)
    if newline is not None:
        bag.error(
            "FL206",
            "a filter condition is one line",
            newline.span,
            hint="join the lines, or end a line with `\\` to continue it",
        )
        return None, bag
    state = _Line(toks, bag)
    try:
        cond = state.condition()
        if not state.at_end():
            tok = toks[state.pos]
            raise state.fail("FL206", f"unexpected {tok.text!r} in filter", tok.span)
    except _LineError:
        return None, bag
    return cond, bag


__all__ = ["ParseResult", "is_uppercase_ident", "parse", "parse_condition", "parse_tokens"]
