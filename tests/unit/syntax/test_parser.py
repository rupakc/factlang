from __future__ import annotations

from decimal import Decimal

from factlang.diagnostics import DiagnosticBag
from factlang.syntax import ast, parse
from factlang.syntax.lexer import tokenize
from factlang.syntax.parser import parse_tokens

LK = ast.LiteralKind


def claim(src: str) -> ast.Claim:
    res = parse(src)
    assert not res.diagnostics.has_errors, res.diagnostics.render(src)
    (item,) = res.document.items
    assert isinstance(item, ast.Claim)
    return item


def codes(src: str) -> list[str]:
    return parse(src).diagnostics.codes()


def names(ref: ast.Ref) -> list[str]:
    return [p.text for p in ref.parts]


def test_simple_claim() -> None:
    c = claim("ROWS orders VALUE >=1000")
    assert c.verb == "ROWS" and c.verb_span.col == 1
    (op,) = c.operands
    assert isinstance(op, ast.Ref) and names(op) == ["orders"]
    (a,) = c.attributes
    assert (a.name, a.op) == ("VALUE", ">=")
    assert a.value.kind is LK.NUMBER and a.value.value == Decimal(1000)
    assert c.filter is None and c.comment is None
    assert c.line_text == "ROWS orders VALUE >=1000"


def test_lowercase_first_token_is_not_a_verb() -> None:
    res = parse("rows orders")
    assert res.diagnostics.codes() == ["FL203"]
    assert "expected a verb" in next(iter(res.diagnostics)).message
    assert res.document.items == ()
    assert codes("'x' orders") == ["FL203"]
    assert codes("_ orders") == ["FL203"]


def test_uppercase_identifier_rule() -> None:
    assert claim("FANOUT t.c MAX_PER_KEY <=3").attributes[0].name == "MAX_PER_KEY"
    # `_` alone has no letter, so it is an operand, not an attribute name
    c = claim("EXISTS _")
    assert isinstance(c.operands[0], ast.Ref)


def test_operands_end_at_first_uppercase_ident() -> None:
    c = claim("UNIT t.c IS bytes")
    assert len(c.operands) == 1
    assert [a.name for a in c.attributes] == ["IS"]
    assert c.attributes[0].value == ast.Literal(LK.IDENT, "bytes", c.attributes[0].value.span)


def test_operand_after_attribute_is_error() -> None:
    assert codes("ROWS t VALUE 1 other") == ["FL203"]


def test_edge_and_refs() -> None:
    c = claim("JOIN orders.customer_id -> customers.id MATCH >=99% CARD N:1 CAST string")
    (edge,) = c.operands
    assert isinstance(edge, ast.Edge)
    assert names(edge.left) == ["orders", "customer_id"]
    assert names(edge.right) == ["customers", "id"]
    m, card, cast = c.attributes
    assert m.value.kind is LK.PERCENT and m.value.value == Decimal("0.99")
    assert card.value.kind is LK.CARDINALITY and card.value.value == "N:1"
    assert cast.value.kind is LK.IDENT


def test_edge_missing_right_side() -> None:
    assert codes("JOIN a.x ->") == ["FL203"]
    assert codes("JOIN a.x -> MATCH >=1") == ["FL203"]


def test_qident_parts() -> None:
    c = claim('EXISTS "My Schema"."ORDERS".`id`')
    (ref,) = c.operands
    assert isinstance(ref, ast.Ref)
    assert [(p.text, p.quoted) for p in ref.parts] == [
        ("My Schema", True),
        ("ORDERS", True),
        ("id", True),
    ]


def test_four_part_ref() -> None:
    (ref,) = claim("EXISTS p.d.t.c").operands
    assert isinstance(ref, ast.Ref) and len(ref.parts) == 4


def test_non_name_ref_part_is_fl203() -> None:
    assert codes("EXISTS a.1:1") == ["FL203"]
    assert codes("EXISTS a.5") == ["FL203"]
    assert codes("EXISTS a.") == ["FL203"]
    assert codes("EXISTS a.'x'") == ["FL203"]


def test_literal_operands() -> None:
    c = claim("FOO 1 'x' 5% 24h")
    assert [o.kind for o in c.operands if isinstance(o, ast.Literal)] == [
        LK.NUMBER,
        LK.STRING,
        LK.PERCENT,
        LK.DURATION,
    ]


def test_attribute_without_op() -> None:
    c = claim("FRESH t.c WITHIN 24h")
    a = c.attributes[0]
    assert a.op is None and a.value.kind is LK.DURATION and a.value.value == 86400


def test_attribute_with_equals_op_raw() -> None:
    assert claim("ROWS t VALUE = 5").attributes[0].op == "="
    assert claim("ROWS t VALUE == 5").attributes[0].op == "=="


def test_list_trailing_comma_and_nesting() -> None:
    a = claim("VALUES t.c IN ['a', 'b',] SPLIT ':'").attributes[0]
    assert a.value.kind is LK.LIST
    items = a.value.value
    assert isinstance(items, tuple) and [x.value for x in items] == ["a", "b"]
    empty = claim("VALUES t.c IN []").attributes[0].value
    assert empty.kind is LK.LIST and empty.value == ()
    nested = claim("X t.c A [[1], 2]").attributes[0].value
    assert isinstance(nested.value, tuple) and nested.value[0].kind is LK.LIST


def test_unclosed_list_fl202() -> None:
    assert codes("VALUES t.c IN ['a', 'b'") == ["FL202"]
    assert codes("VALUES t.c IN [") == ["FL202"]
    assert codes("VALUES t.c IN ['a'") == ["FL202"]


def test_bad_list_separator() -> None:
    assert codes("VALUES t.c IN [1 2]") == ["FL203"]
    assert codes("VALUES t.c IN [,]") == ["FL201"]


def test_attribute_without_value_fl201() -> None:
    assert codes("NULLS t.c RATE") == ["FL201"]
    assert codes("NULLS t.c RATE <=") == ["FL201"]
    assert codes("NULLS t.c RATE WHERE x = 1") == ["FL201"]
    assert codes("NULLS t.c RATE ,") == ["FL201"]


def test_duplicate_attribute_fl204() -> None:
    assert codes("ROWS t VALUE >=1 VALUE <=5") == ["FL204"]


def test_typed_attribute_values() -> None:
    a = claim("RANGE t.c BETWEEN [DATE '2020-01-01', TIMESTAMP '2021-01-01T00:00:00Z']").attributes[
        0
    ]
    lo, hi = a.value.value  # type: ignore[misc]
    assert (lo.kind, lo.value) == (LK.DATE, "2020-01-01")
    assert (hi.kind, hi.value) == (LK.TIMESTAMP, "2021-01-01T00:00:00Z")


def test_where_starts_filter_and_runs_to_end() -> None:
    c = claim("RANGE t.c BETWEEN [0, 100] WHERE x = 1")
    assert [a.name for a in c.attributes] == ["BETWEEN"]
    assert isinstance(c.filter, ast.Filter)
    assert isinstance(c.filter.condition, ast.Comparison)
    assert c.filter.condition.column.text == "x"


def test_attribute_after_filter_is_fl206() -> None:
    assert codes("ROWS t WHERE x = 1 VALUE >=1") == ["FL206"]


def test_uppercase_word_after_filter_is_fl206() -> None:
    assert codes("ROWS t WHERE x = 1 RATE") == ["FL206"]


def test_filter_precedence() -> None:
    f = claim("ROWS t WHERE a = 1 OR b = 2 AND c = 3").filter
    assert f is not None
    cond = f.condition
    assert isinstance(cond, ast.Or) and len(cond.items) == 2
    assert isinstance(cond.items[0], ast.Comparison)
    assert isinstance(cond.items[1], ast.And) and len(cond.items[1].items) == 2


def test_not_binds_tighter_than_and() -> None:
    f = claim("ROWS t WHERE NOT a = 1 AND b = 2").filter
    assert f is not None and isinstance(f.condition, ast.And)
    assert isinstance(f.condition.items[0], ast.Not)


def test_parens() -> None:
    f = claim("ROWS t WHERE (a = 1 OR b = 2) AND c = 3").filter
    assert f is not None and isinstance(f.condition, ast.And)
    assert isinstance(f.condition.items[0], ast.Or)
    assert codes("ROWS t WHERE (a = 1") == ["FL206"]


def test_filter_comparison_forms() -> None:
    def cond(text: str) -> ast.Cond:
        f = claim(f"ROWS t WHERE {text}").filter
        assert f is not None
        return f.condition

    c = cond("a >= 5")
    assert isinstance(c, ast.Comparison) and c.op == ">=" and c.value.value == Decimal(5)
    c = cond("a != 'x'")
    assert isinstance(c, ast.Comparison) and c.value.kind is LK.STRING
    c = cond("a == TRUE")
    assert isinstance(c, ast.Comparison) and c.value == ast.Literal(LK.BOOL, True, c.value.span)
    c = cond("a = -3")
    assert isinstance(c, ast.Comparison) and c.value.value == Decimal(-3)
    c = cond("a = DATE '2025-01-31'")
    assert isinstance(c, ast.Comparison) and (c.value.kind, c.value.value) == (
        LK.DATE,
        "2025-01-31",
    )
    c = cond("a < TIMESTAMP '2025-01-31T12:00:00Z'")
    assert isinstance(c, ast.Comparison) and c.value.kind is LK.TIMESTAMP
    c = cond("a IN [1, 2,]")
    assert isinstance(c, ast.InList) and not c.negated and len(c.values) == 2
    c = cond("a NOT IN ['x']")
    assert isinstance(c, ast.InList) and c.negated
    c = cond("a IS NULL")
    assert isinstance(c, ast.IsNull) and not c.negated
    c = cond("a IS NOT NULL")
    assert isinstance(c, ast.IsNull) and c.negated
    c = cond("a BETWEEN 1 AND 5")
    assert isinstance(c, ast.Between) and c.low.value == 1 and c.high.value == 5
    c = cond('"my col" = 1')
    assert isinstance(c, ast.Comparison) and c.column.quoted and c.column.text == "my col"


def test_between_and_does_not_split_conjunction() -> None:
    f = claim("ROWS t WHERE a BETWEEN 1 AND 5 AND b = 2").filter
    assert f is not None and isinstance(f.condition, ast.And)
    assert isinstance(f.condition.items[0], ast.Between)


def test_null_comparison_fl206() -> None:
    assert codes("ROWS t WHERE x = NULL") == ["FL206"]
    assert codes("ROWS t WHERE x != NULL") == ["FL206"]


def test_malformed_filters_fl206() -> None:
    for tail in [
        "",
        "x =",
        "= 1",
        "x = 1 AND",
        "x IS",
        "x IS 5",
        "x BETWEEN 1 5",
        "x BETWEEN 1 AND",
        "x IN 1",
        "x IN (1, 2)",
        "x NOT 1",
        "t.x = 1",
        "x = 1 y = 2",
        "x = 5%",
        "AND = 1",
        "x = DATE",
        "WHERE x = 1",
    ]:
        assert codes(f"ROWS t WHERE {tail}") == ["FL206"], tail


def test_filter_keywords_case_sensitive() -> None:
    # lowercase `is`/`null` are plain identifiers inside a filter, so this is malformed
    assert codes("ROWS t WHERE x is null") == ["FL206"]
    # a quoted keyword is a column name
    f = claim('ROWS t WHERE "AND" = 1').filter
    assert f is not None and isinstance(f.condition, ast.Comparison)
    assert codes("ROWS t WHERE AND = 1") == ["FL206"]


def test_unclosed_list_in_filter() -> None:
    assert codes("ROWS t WHERE x IN [1, 2") == ["FL202"]


def test_use_directive() -> None:
    res = parse("USE a.b\nUSE c # note\n")
    d1, d2 = res.document.items
    assert isinstance(d1, ast.Directive) and names(d1.namespace) == ["a", "b"]
    assert isinstance(d2, ast.Directive) and d2.comment == "note"
    assert not res.diagnostics.has_errors


def test_use_must_be_alone() -> None:
    assert codes("USE a.b MATCH >=1") == ["FL203"]
    assert codes("USE") == ["FL203"]
    assert codes("USE 5") == ["FL203"]


def test_use_mid_line_is_attribute_name() -> None:
    c = claim("NOTE t.c USE x")
    assert [a.name for a in c.attributes] == ["USE"]
    assert not isinstance(c, ast.Directive)


def test_comments_preserved() -> None:
    c = claim("ROWS orders VALUE >=1  #  large table ")
    assert c.comment == "large table"
    res = parse("# only a comment\n\nROWS t VALUE 1\n")
    assert len(res.document.items) == 1


def test_continuation_joins_lines() -> None:
    src = "VALUES t.c IN ['a', \\\n  'b']"
    c = claim(src)
    assert c.attributes[0].value.kind is LK.LIST
    assert c.line_text == src


def test_recovery_per_line() -> None:
    src = "\n".join(
        [
            "ROWS a VALUE >=1",
            "rows b",
            "NULLS a.c RATE",
            "VALUES a.c IN ['x'",
            "ROWS a VALUE 1 VALUE 2",
            "ROWS a WHERE x = NULL",
            "UNIQUE a.id",
            "USE x.y",
        ]
    )
    res = parse(src)
    assert res.diagnostics.codes() == ["FL203", "FL201", "FL202", "FL204", "FL206"]
    assert [type(i).__name__ for i in res.document.items] == ["Claim", "Claim", "Directive"]
    first, second, _ = res.document.items
    assert isinstance(first, ast.Claim) and first.verb == "ROWS"
    assert isinstance(second, ast.Claim) and second.verb == "UNIQUE"


def test_lexer_errors_pass_through_and_line_is_skipped() -> None:
    res = parse("ROWS a VALUE ?\nROWS b VALUE 1")
    assert res.diagnostics.codes() == ["FL102"]
    assert len(res.document.items) == 1
    assert codes("ROWS t.c WHERE x = 'abc") == ["FL101"]
    assert codes("ROWS a \\") == ["FL205"]


def test_diagnostic_spans_point_at_the_problem() -> None:
    res = parse("ROWS t VALUE 1 VALUE 2")
    (d,) = list(res.diagnostics)
    assert d.span is not None and (d.span.line, d.span.col) == (1, 16)


def test_parse_tokens_without_source() -> None:
    bag = DiagnosticBag()
    doc = parse_tokens(tokenize("ROWS t VALUE 1", bag), bag)
    (c,) = doc.items
    assert isinstance(c, ast.Claim) and c.line_text == "ROWS t VALUE 1" and doc.source == ""


def test_parse_result_and_document_source() -> None:
    src = "ROWS t VALUE 1\r\nEXISTS t\r\n"
    res = parse(src)
    assert res.document.source == src
    assert [i.line_text for i in res.document.items if isinstance(i, ast.Claim)] == [
        "ROWS t VALUE 1",
        "EXISTS t",
    ]


def test_empty_and_whitespace_documents() -> None:
    assert parse("").document.items == ()
    assert parse("  \n\n").document.items == ()


def test_never_raises_on_garbage() -> None:
    for s in ["]]]", "->", "WHERE", "A B C", "'", "((", "ROWS t WHERE (", "1 2 3", "\\"]:
        parse(s)


def test_deep_nesting_is_fl207_not_recursion_error() -> None:
    for src in [
        "ROWS o WHERE " + "(" * 3000,
        "ROWS o WHERE " + "NOT " * 3000 + "a = 1",
        "ROWS o X " + "[" * 3000,
        "ROWS o WHERE " + "(" * 65 + "a = 1" + ")" * 65,
    ]:
        res = parse(src)
        assert res.diagnostics.codes() == ["FL207"], src[:30]
        assert res.document.items == ()


def test_nesting_depth_ten_still_parses() -> None:
    assert not parse("ROWS o WHERE " + "(" * 10 + "a = 1" + ")" * 10).diagnostics.has_errors
    assert not parse("ROWS o WHERE " + "NOT " * 10 + "a = 1").diagnostics.has_errors
    assert not parse("ROWS o X " + "[" * 10 + "]" * 10).diagnostics.has_errors
    assert not parse("ROWS o WHERE " + "(" * 64 + "a = 1" + ")" * 64).diagnostics.has_errors


def test_bare_boolean_atom() -> None:
    def cond(text: str) -> ast.Cond:
        f = claim(f"ROWS t WHERE {text}").filter
        assert f is not None
        return f.condition

    c = cond("deleted")
    assert isinstance(c, ast.FilterName) and c.column.text == "deleted" and not c.column.quoted
    c = cond('"my flag"')
    assert isinstance(c, ast.FilterName) and c.column.quoted and c.column.text == "my flag"
    c = cond("NOT deleted")
    assert isinstance(c, ast.Not) and isinstance(c.item, ast.FilterName)
    c = cond("deleted IS NULL OR NOT deleted")
    assert isinstance(c, ast.Or) and isinstance(c.items[0], ast.IsNull)
    assert isinstance(c.items[1], ast.Not) and isinstance(c.items[1].item, ast.FilterName)
    c = cond("NOT (paid) AND amount > 0")
    assert isinstance(c, ast.And) and isinstance(c.items[0], ast.Not)
    assert isinstance(c.items[0].item, ast.FilterName)
    c = cond("(a) AND b")
    assert isinstance(c, ast.And) and all(isinstance(i, ast.FilterName) for i in c.items)


def test_bare_atom_does_not_break_comparisons() -> None:
    for text in ["a = 1", "a IN [1]", "a NOT IN [1]", "a IS NULL", "a BETWEEN 1 AND 2"]:
        f = claim(f"ROWS t WHERE {text}").filter
        assert f is not None and not isinstance(f.condition, ast.FilterName), text


def test_bare_atom_malformed_fl206() -> None:
    for tail in [
        "deleted deleted",
        "TRUE",
        "FALSE",
        "NOT TRUE",
        "NOT FALSE",
        "a AND TRUE",
        "deleted NOT paid",
        "t.deleted",
        "deleted = ",
    ]:
        assert codes(f"ROWS t WHERE {tail}") == ["FL206"], tail
    f = claim('ROWS t WHERE "TRUE"').filter
    assert f is not None and isinstance(f.condition, ast.FilterName)


def test_not_before_bare_name_counts_toward_depth() -> None:
    assert codes("ROWS t WHERE " + "NOT " * 70 + "a") == ["FL207"]
    assert codes("ROWS t WHERE " + "NOT " * 10 + "a") == []


def test_filter_keywords_are_public_and_the_parsers_own() -> None:
    from factlang import syntax
    from factlang.syntax import parser, tokens

    assert syntax.FILTER_KEYWORDS is tokens.FILTER_KEYWORDS
    assert "FILTER_KEYWORDS" in syntax.__all__
    assert parser._FILTER_KEYWORDS is tokens.FILTER_KEYWORDS
    assert isinstance(tokens.FILTER_KEYWORDS, frozenset)
    keywords = tokens.FILTER_KEYWORDS
    assert sorted(keywords) == sorted(
        ["AND", "OR", "NOT", "IN", "IS", "NULL", "BETWEEN", "TRUE", "FALSE", "DATE", "TIMESTAMP"]
    )
