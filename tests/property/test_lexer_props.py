from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from factlang.diagnostics import DiagnosticBag
from factlang.syntax.lexer import tokenize
from factlang.syntax.tokens import TokenKind


@settings(max_examples=500, deadline=None)
@given(st.text())
def test_tokenize_never_raises(source: str) -> None:
    toks = tokenize(source, DiagnosticBag())
    assert toks and toks[-1].kind is TokenKind.EOF


@settings(max_examples=500, deadline=None)
@given(st.text(alphabet=st.characters(), max_size=200))
def test_spans_within_source(source: str) -> None:
    src = source.removeprefix("﻿").replace("\r\n", "\n").replace("\r", "\n")
    lines = src.split("\n")
    for tok in tokenize(source, DiagnosticBag()):
        s = tok.span
        assert 1 <= s.line <= len(lines)
        # a NEWLINE token occupies the column just past its line's last character
        slack = 2 if tok.kind is TokenKind.NEWLINE else 1
        assert 1 <= s.col <= s.end_col <= len(lines[s.line - 1]) + slack
