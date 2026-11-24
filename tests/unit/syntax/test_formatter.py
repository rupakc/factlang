from __future__ import annotations

from factlang.syntax import ast, parse
from factlang.syntax.formatter import format_claim, format_document


def fmt(src: str) -> str:
    res = parse(src)
    assert not res.diagnostics.has_errors, res.diagnostics.render(src)
    return format_document(res.document).rstrip("\n")


def test_canonical_spacing() -> None:
    assert (
        fmt("ROWS   orders   VALUE>=1_000   WHERE  a=1") == "ROWS orders VALUE >= 1000 WHERE a = 1"
    )
    assert fmt("JOIN a.x->b.y CARD 1:N") == "JOIN a.x -> b.y CARD 1:N"
    assert fmt("ROWS t\\\n  VALUE 5") == "ROWS t VALUE 5"


def test_eq_canonicalised() -> None:
    assert fmt("ROWS t VALUE = 5") == "ROWS t VALUE == 5"
    assert fmt("ROWS t VALUE == 5 WHERE a = 1") == "ROWS t VALUE == 5 WHERE a = 1"


def test_comment_preserved() -> None:
    assert fmt("ROWS t VALUE 5 # hello  ") == "ROWS t VALUE 5  # hello"
    assert fmt("USE a.b   # ns") == "USE a.b  # ns"
    assert fmt("ROWS t #") == "ROWS t  #"


def test_quoted_names_roundtrip() -> None:
    assert fmt('ROWS "My Table".`a"b`') == 'ROWS "My Table"."a""b"'
    assert fmt("ROWS t WHERE `x y` = 'it''s'") == "ROWS t WHERE \"x y\" = 'it''s'"
    assert fmt('ROWS "plain"') == 'ROWS "plain"'  # re-quoted only if it was quoted


def test_numbers_percents_durations() -> None:
    assert fmt("FRESH t MAX 90m") == "FRESH t MAX 90m"
    assert fmt("FRESH t MAX 120m") == "FRESH t MAX 2h"
    assert fmt("FRESH t MAX 7d") == "FRESH t MAX 1w"
    assert fmt("FRESH t MAX 61s") == "FRESH t MAX 61s"
    assert fmt("NULLS t.c MAX 5%") == "NULLS t.c MAX 5%"
    assert fmt("NULLS t.c MAX 0.50%") == "NULLS t.c MAX 0.5%"
    assert fmt("ROWS t VALUE 1.500") == "ROWS t VALUE 1.5"
    assert fmt("ROWS t VALUE 1e3") == "ROWS t VALUE 1000"
    assert fmt("ROWS t VALUE >= -2.50") == "ROWS t VALUE >= -2.5"


def test_lists_and_typed_values() -> None:
    assert fmt("RANGE t.c BETWEEN [0,100,]") == "RANGE t.c BETWEEN [0, 100]"
    assert fmt("RANGE t.c MIN DATE '2025-01-31'") == "RANGE t.c MIN DATE '2025-01-31'"
    assert fmt("VALUES t.c IN []") == "VALUES t.c IN []"


def test_filter_precedence() -> None:
    assert fmt("ROWS t WHERE a=1 OR b=2 AND c=3") == "ROWS t WHERE a = 1 OR b = 2 AND c = 3"
    assert fmt("ROWS t WHERE (a=1 OR b=2) AND c=3") == "ROWS t WHERE (a = 1 OR b = 2) AND c = 3"
    assert fmt("ROWS t WHERE (a=1 AND b=2) OR c=3") == "ROWS t WHERE a = 1 AND b = 2 OR c = 3"
    assert fmt("ROWS t WHERE NOT (a=1 AND b=2)") == "ROWS t WHERE NOT (a = 1 AND b = 2)"
    assert fmt("ROWS t WHERE NOT NOT a=1") == "ROWS t WHERE NOT NOT a = 1"
    assert fmt("ROWS t WHERE ((a=1))") == "ROWS t WHERE a = 1"


def test_filter_forms() -> None:
    src = (
        "ROWS t WHERE a IN [1, 'x'] AND b NOT IN [TRUE] AND c IS NULL AND d IS NOT NULL "
        "AND e BETWEEN 1 AND 2 AND f >= TIMESTAMP '2025-01-31T12:00:00Z' AND g = DATE '2025-01-01'"
    )
    assert fmt(src) == src.replace("AND c IS", "AND c IS")
    assert fmt("ROWS t WHERE a  IS   NOT  NULL") == "ROWS t WHERE a IS NOT NULL"


def test_attribute_source_order_kept() -> None:
    assert fmt("ROWS t MAX 2 MIN 1") == "ROWS t MAX 2 MIN 1"


def test_document_lines_and_format_claim() -> None:
    res = parse("USE a.b\n\nROWS t VALUE 1 # c\r\nUNIQUE t.id\n")
    assert format_document(res.document) == "USE a.b\nROWS t VALUE 1  # c\nUNIQUE t.id\n"
    claim = res.document.items[1]
    assert isinstance(claim, ast.Claim)
    assert format_claim(claim) == "ROWS t VALUE 1  # c"
    assert format_document(parse("").document) == ""
