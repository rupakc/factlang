from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest

from factlang.ir import Arity, ColumnAggregate, Metric
from factlang.semantics.filters import Param
from factlang.semantics.refs import Ident, TableRef
from factlang.sql.probe import PARAMSTYLES, RenderedProbe, RenderError

T = TableRef(None, None, Ident("orders"))
PLAN = ColumnAggregate(T, None, (Metric.ROW_COUNT,))
P1 = Param("p1", "paid", "string")
P2 = Param("p2", 7, "integer")
TEMPLATE = "SELECT COUNT(*) AS row_count FROM orders WHERE s = {{p1}} AND n > {{p2}} OR s = {{p1}}"


def probe(template: str = TEMPLATE, params: tuple[Param, ...] = (P1, P2)) -> RenderedProbe:
    return RenderedProbe(template, params, Arity.SINGLE_ROW, PLAN, "duckdb", "verified")


def test_bind_qmark_repeats_reused_params_in_occurrence_order() -> None:
    sql, values = probe().bind("qmark")
    assert sql == "SELECT COUNT(*) AS row_count FROM orders WHERE s = ? AND n > ? OR s = ?"
    assert values == ("paid", 7, "paid")


def test_bind_numeric_numbers_by_param_position() -> None:
    sql, values = probe().bind("numeric")
    assert sql.endswith("s = :1 AND n > :2 OR s = :1")
    assert values == ("paid", 7)


def test_bind_named() -> None:
    sql, values = probe().bind("named")
    assert sql.endswith("s = :p1 AND n > :p2 OR s = :p1")
    assert values == {"p1": "paid", "p2": 7}


def test_bind_format_escapes_literal_percent() -> None:
    sql, values = probe("SELECT 5 % 2 AS x FROM t WHERE s = {{p1}}", (P1,)).bind("format")
    assert sql == "SELECT 5 %% 2 AS x FROM t WHERE s = %s"
    assert values == ("paid",)


def test_bind_pyformat_escapes_literal_percent() -> None:
    sql, values = probe("SELECT 5 % 2 AS x FROM t WHERE s = {{p1}}", (P1,)).bind("pyformat")
    assert sql == "SELECT 5 %% 2 AS x FROM t WHERE s = %(p1)s"
    assert values == {"p1": "paid"}


def test_bind_bigquery() -> None:
    sql, values = probe().bind("bigquery")
    assert sql.endswith("s = @p1 AND n > @p2 OR s = @p1")
    assert values == {"p1": "paid", "p2": 7}


def test_qmark_and_named_do_not_double_percent() -> None:
    sql, _ = probe("SELECT 5 % 2 AS x FROM t WHERE s = {{p1}}", (P1,)).bind("qmark")
    assert "5 % 2" in sql


def test_every_paramstyle_is_covered() -> None:
    assert set(PARAMSTYLES) == {"qmark", "numeric", "named", "format", "pyformat", "bigquery"}
    for style in PARAMSTYLES:
        sql, _ = probe().bind(style)
        assert "{{" not in sql


def test_unknown_paramstyle_is_refused() -> None:
    with pytest.raises(ValueError, match="paramstyle"):
        probe().bind("dollar")


def test_unknown_token_is_a_render_error() -> None:
    with pytest.raises(RenderError, match="FL500"):
        probe("SELECT {{p9}} AS x", (P1,)).bind("qmark")


def test_duplicate_param_names_are_refused() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        probe(params=(P1, Param("p1", "x", "string")))


def test_inline_default_ansi_quoting_doubles_quotes() -> None:
    hostile = "x'; DROP TABLE orders; --"
    text = probe(
        "SELECT 1 AS x FROM t WHERE s = {{p1}}", (Param("p1", hostile, "string"),)
    ).inline()
    assert text == "SELECT 1 AS x FROM t WHERE s = 'x''; DROP TABLE orders; --'"


def test_inline_custom_quote_function() -> None:
    text = probe("SELECT {{p1}} AS x", (P1,)).inline(lambda s: '"' + s + '"')
    assert text == 'SELECT "paid" AS x'


def test_inline_typed_values() -> None:
    params = (
        Param("a", 3, "integer"),
        Param("b", Decimal("2.50"), "decimal"),
        Param("c", True, "boolean"),
        Param("d", dt.date(2025, 1, 31), "date"),
        Param("e", dt.datetime(2025, 1, 31, 12, tzinfo=dt.timezone.utc), "timestamp"),
        Param("f", None, "string"),
    )
    text = probe("{{a}} {{b}} {{c}} {{d}} {{e}} {{f}}", params).inline()
    assert text == ("3 2.50 TRUE DATE '2025-01-31' TIMESTAMP '2025-01-31T12:00:00+00:00' NULL")


def test_hostile_literal_never_in_sql_template_only_in_params() -> None:
    hostile = "'); DELETE FROM orders; --"
    p = probe("SELECT 1 AS x FROM t WHERE s = {{p1}}", (Param("p1", hostile, "string"),))
    for style in PARAMSTYLES:
        sql, values = p.bind(style)
        assert hostile not in sql
        flat = values.values() if isinstance(values, dict) else values
        assert hostile in flat
