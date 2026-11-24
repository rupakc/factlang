from __future__ import annotations

import decimal
from decimal import Decimal

from factlang.diagnostics import DiagnosticBag
from factlang.syntax.lexer import tokenize
from factlang.syntax.tokens import Token, TokenKind

K = TokenKind


def lex(src: str) -> tuple[list[Token], DiagnosticBag]:
    bag = DiagnosticBag()
    return tokenize(src, bag), bag


def kinds(src: str) -> list[TokenKind]:
    toks, _ = lex(src)
    return [t.kind for t in toks][:-1]


def test_empty_ends_with_eof() -> None:
    toks, bag = lex("")
    assert [t.kind for t in toks] == [K.EOF] and len(bag) == 0


def test_cardinality_before_number() -> None:
    toks, bag = lex("N:1 1:1")
    assert [t.kind for t in toks][:-1] == [K.CARDINALITY, K.CARDINALITY]
    assert [t.text for t in toks][:-1] == ["N:1", "1:1"]
    assert len(bag) == 0
    assert kinds("1:N N:M") == [K.CARDINALITY, K.CARDINALITY]


def test_duration() -> None:
    toks, _ = lex("24h")
    assert toks[0].kind is K.DURATION and toks[0].value == 86400
    for text, secs in [("30s", 30), ("5m", 300), ("2d", 172800), ("1w", 604800)]:
        assert lex(text)[0][0].value == secs


def test_duration_requires_single_unit_letter() -> None:
    toks, _ = lex("24hours")
    assert [t.kind for t in toks][:-1] == [K.NUMBER, K.IDENT]
    assert toks[0].value == Decimal(24) and toks[1].text == "hours"
    assert kinds("24x") == [K.NUMBER, K.IDENT]


def test_two_char_ops_first() -> None:
    for op in [">=", "<=", "==", "!="]:
        toks, _ = lex(op)
        assert [t.kind for t in toks][:-1] == [K.OP] and toks[0].text == op
    assert [t.text for t in lex("= < >")[0]][:-1] == ["=", "<", ">"]


def test_arrow_not_negative() -> None:
    assert kinds("a->b") == [K.IDENT, K.ARROW, K.IDENT]
    assert kinds("a->-5") == [K.IDENT, K.ARROW, K.NUMBER]


def test_negative_number() -> None:
    toks, _ = lex("-5")
    assert toks[0].kind is K.NUMBER and toks[0].value == Decimal(-5) and toks[0].text == "-5"
    assert lex("= -2.5e1")[0][1].value == Decimal("-25")


def test_minus_before_digit_is_always_a_sign() -> None:
    toks, bag = lex("5 -3")
    assert [t.kind for t in toks][:-1] == [K.NUMBER, K.NUMBER]
    assert toks[1].value == Decimal(-3) and len(bag) == 0
    for src in ["x BETWEEN -1 AND 5", "ROWS t -5", "MIN -5", "a.b -2", "[1] -2", "(x) -2"]:
        toks, bag = lex(src)
        assert len(bag) == 0 and K.ERROR not in [t.kind for t in toks]
    assert [t.value for t in lex("MIN -5")[0]][1] == Decimal(-5)


def test_lone_minus_is_FL102() -> None:
    toks, bag = lex("a - b")
    assert bag.codes() == ["FL102"] and K.ERROR in [t.kind for t in toks]


def test_percent_normalised() -> None:
    toks, _ = lex("95%")
    assert toks[0].kind is K.PERCENT and toks[0].value == Decimal("0.95")
    assert lex("12.5%")[0][0].value == Decimal("0.125")
    assert lex("100%")[0][0].value == Decimal("1")


def test_underscore_digits() -> None:
    toks, _ = lex("1_717")
    assert toks[0].kind is K.NUMBER and toks[0].value == Decimal(1717)
    assert toks[0].text == "1_717"


def test_number_forms() -> None:
    assert lex("3.14")[0][0].value == Decimal("3.14")
    assert lex("1e3")[0][0].value == Decimal("1e3")
    assert kinds("1.") == [K.NUMBER, K.DOT]
    assert kinds("1e") == [K.NUMBER, K.IDENT]


def test_string_escape() -> None:
    toks, _ = lex("'it''s'")
    assert toks[0].kind is K.STRING and toks[0].value == "it's"


def test_qident_double_quote_and_backtick() -> None:
    toks, _ = lex('"Order Items"')
    assert toks[0].kind is K.QIDENT and toks[0].value == "Order Items"
    toks, _ = lex("`x``y`")
    assert toks[0].kind is K.QIDENT and toks[0].value == "x`y"
    assert toks[0].text == "`x``y`"
    assert lex('"a""b"')[0][0].value == 'a"b'


def test_unterminated_string_FL101() -> None:
    toks, bag = lex("'abc\nNEXT")
    assert bag.codes() == ["FL101"]
    assert [t.kind for t in toks] == [K.ERROR, K.NEWLINE, K.IDENT, K.EOF]
    toks, bag = lex('"abc')
    assert bag.codes() == ["FL101"] and toks[0].kind is K.ERROR


def test_unexpected_char_FL102() -> None:
    toks, bag = lex("a:b")
    assert bag.codes() == ["FL102"]
    d = next(iter(bag))
    assert d.span is not None and d.span.col == 2
    assert [t.kind for t in toks] == [K.IDENT, K.ERROR, K.IDENT, K.EOF]


def test_comment_kept() -> None:
    toks, _ = lex("a # note\nb")
    assert [t.kind for t in toks] == [K.IDENT, K.COMMENT, K.NEWLINE, K.IDENT, K.EOF]
    assert toks[1].text == "# note"


def test_continuation_joins_lines() -> None:
    toks, bag = lex("a \\\nb")
    assert [t.kind for t in toks] == [K.IDENT, K.IDENT, K.EOF]
    assert toks[1].span.line == 2 and toks[1].span.col == 1
    assert len(bag) == 0


def test_dangling_continuation_FL205() -> None:
    toks, bag = lex("a \\")
    assert bag.codes() == ["FL205"]
    assert [t.kind for t in toks] == [K.IDENT, K.EOF]


def test_crlf_equivalent_to_lf() -> None:
    def shape(src: str) -> list[tuple[TokenKind, str, object, object]]:
        return [(t.kind, t.text, t.value, t.span) for t in lex(src)[0]]

    lf = "A t.c\nB 'x'\n# c\nD"
    assert shape(lf.replace("\n", "\r\n")) == shape(lf)
    assert shape(lf.replace("\n", "\r")) == shape(lf)


def test_bom_ignored() -> None:
    toks, _ = lex("\ufeffA")
    assert toks[0].kind is K.IDENT and toks[0].span.col == 1


def test_spans_are_1_based() -> None:
    toks, _ = lex("ab cd\n  'x'")
    assert (toks[0].span.line, toks[0].span.col, toks[0].span.end_col) == (1, 1, 3)
    assert (toks[1].span.line, toks[1].span.col, toks[1].span.end_col) == (1, 4, 6)
    assert (toks[3].span.line, toks[3].span.col, toks[3].span.end_col) == (2, 3, 6)


def test_punctuation_and_idents() -> None:
    assert kinds("t.c, [x] (y) $a_1") == [
        K.IDENT, K.DOT, K.IDENT, K.COMMA, K.LBRACKET, K.IDENT, K.RBRACKET,
        K.LPAREN, K.IDENT, K.RPAREN, K.ERROR, K.IDENT,
    ]  # fmt: skip


def test_huge_numbers_do_not_raise() -> None:
    toks, _ = lex("1e99999999999999999999999 " + "9" * 5000 + "h 5e999999%")
    assert toks[-1].kind is K.EOF


def test_negative_percent_and_exponent_percent() -> None:
    toks, _ = lex("-5%")
    assert toks[0].kind is K.PERCENT and toks[0].value == Decimal("-0.05")
    toks, _ = lex("1e5%")
    assert toks[0].kind is K.PERCENT and toks[0].value == Decimal("1e3")


def test_percent_exact_beyond_60_digits() -> None:
    digits = "1" + "0" * 69 + "7"
    toks, _ = lex(digits + "%")
    assert toks[0].value == Decimal(digits).scaleb(-2, context=decimal.Context(prec=200))


def test_unterminated_backtick_qident_FL101() -> None:
    toks, bag = lex("`abc")
    assert bag.codes() == ["FL101"] and toks[0].kind is K.ERROR


def test_cardinality_only_before_non_identifier_char() -> None:
    assert kinds("1:1x") == [K.NUMBER, K.ERROR, K.NUMBER, K.IDENT]
    assert kinds("N:Mx") == [K.IDENT, K.ERROR, K.IDENT]
    assert kinds("1:1)") == [K.CARDINALITY, K.RPAREN]
