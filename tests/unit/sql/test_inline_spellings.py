"""Inline literal spellings and precision caps of 0.1.3 (spec 3.2: I1, I2, I3).

Display/inline path only: bound SQL is pinned by ``test_bound_sql_unchanged.py``. Every new
spelling is checked by a sqlglot round-trip in its own dialect (parse -> one literal of the
expected kind and text -> generate -> parse again, same literal); the ANSI timestamp form is
executed by DuckDB.
"""

from __future__ import annotations

import datetime as dt
import math
from decimal import Decimal

import pytest

from factlang.engine import display_sql, string_quoter
from factlang.ir import Arity, ColumnAggregate, Metric
from factlang.semantics.filters import Param
from factlang.semantics.refs import Ident, TableRef
from factlang.sql.probe import ANSI_STYLE, LiteralStyle, RenderedProbe, RenderError

PLAN = ColumnAggregate(TableRef(None, None, Ident("orders")), None, (Metric.ROW_COUNT,))
FL507 = r"^FL507 cannot inline"
AWARE = dt.datetime(2020, 1, 1, 0, 0, 0, 250, tzinfo=dt.timezone(dt.timedelta(hours=5)))
WHOLE = dt.datetime(2025, 1, 31, 12)  # naive = UTC, no microseconds

UTC_FORM = ("TIMESTAMP '2019-12-31 19:00:00.000250 UTC'", "TIMESTAMP '2025-01-31 12:00:00 UTC'")
NAIVE_FORM = ("TIMESTAMP '2019-12-31 19:00:00.000250'", "TIMESTAMP '2025-01-31 12:00:00'")
CAST_Z_FORM = (
    "CAST('2019-12-31 19:00:00.000250Z' AS TIMESTAMP)",
    "CAST('2025-01-31 12:00:00Z' AS TIMESTAMP)",
)
ANSI_FORM = (
    "TIMESTAMP '2019-12-31T19:00:00.000250+00:00'",
    "TIMESTAMP '2025-01-31T12:00:00+00:00'",
)
TIMESTAMP_SPELLINGS = {
    "trino": UTC_FORM,
    "presto": UTC_FORM,
    "athena": UTC_FORM,
    "dune": UTC_FORM,  # a sqlglot Trino subclass: the ancestor walk, no table entry
    # 0.1.4: a zone-less literal is read in the session time zone (Spark >= 3.0, Databricks)
    # or the JVM default zone (Spark 2.x), so the zone is explicit
    "databricks": UTC_FORM,
    "spark": UTC_FORM,
    "spark2": CAST_Z_FORM,  # Spark 2.x's TIMESTAMP '...' literal rejects a zone suffix
    # zone-less (local date-time) TIMESTAMP types: a naive UTC wall-clock is the right value
    "hive": NAIVE_FORM,
    "teradata": NAIVE_FORM,
    "exasol": NAIVE_FORM,
}


def probe(value: object, family: str, dialect: str = "duckdb") -> RenderedProbe:
    return RenderedProbe(
        "SELECT {{p1}} AS x", (Param("p1", value, family),), Arity.SINGLE_ROW, PLAN, dialect, "v"
    )


def inline(value: object, family: str, dialect: str = "duckdb") -> str:
    return probe(value, family, dialect).inline(string_quoter(dialect))


def quoted_text(timestamp_literal: str) -> str:
    """The string inside a ``TIMESTAMP '...'`` or ``CAST('...' AS TIMESTAMP)`` literal."""
    start = timestamp_literal.index("'") + 1
    return timestamp_literal[start : timestamp_literal.index("'", start)]


def literal(sql: str) -> str:
    prefix, suffix = "SELECT ", " AS x"
    assert sql.startswith(prefix) and sql.endswith(suffix), sql
    return sql[len(prefix) : -len(suffix)]


# -- I1 timestamps ------------------------------------------------------------------------------


@pytest.mark.parametrize("dialect", sorted(TIMESTAMP_SPELLINGS))
def test_timestamp_spelling_per_dialect(dialect: str) -> None:
    if dialect not in ("trino", "databricks"):
        pytest.importorskip("sqlglot")
    fractional, whole = TIMESTAMP_SPELLINGS[dialect]
    assert literal(inline(AWARE, "timestamp", dialect)) == fractional
    assert literal(inline(WHOLE, "timestamp", dialect)) == whole
    utc_naive = dt.datetime(2025, 1, 31, 12, tzinfo=dt.timezone.utc)
    assert literal(inline(utc_naive, "timestamp", dialect)) == whole


@pytest.mark.parametrize("dialect", sorted(TIMESTAMP_SPELLINGS))
def test_every_new_timestamp_spelling_round_trips_through_sqlglot(dialect: str) -> None:
    sqlglot = pytest.importorskip("sqlglot")
    from sqlglot import exp

    for value in (AWARE, WHOLE):
        sql = inline(value, "timestamp", dialect)
        text = quoted_text(literal(sql))
        tree = sqlglot.parse_one(sql, read=dialect)
        node = tree.expressions[0].this
        assert isinstance(node, exp.Cast), (dialect, sql, repr(node))
        assert node.this.is_string and node.this.this == text
        assert node.to.is_type(*exp.DataType.TEMPORAL_TYPES), node.to
        tz_typed = node.to.this in (exp.DataType.Type.TIMESTAMPTZ,)
        if text.endswith((" UTC", "Z")):
            assert tz_typed, (dialect, node.to.sql(dialect))
        again = sqlglot.parse_one(tree.sql(dialect=dialect), read=dialect).expressions[0].this
        assert isinstance(again, exp.Cast) and again.this.this == text
        assert again.to.this is node.to.this


SESSION_LOCAL = ("databricks", "spark")  # 0.1.4: Spark >= 3.0 semantics, explicit UTC
ZONE_LESS = ("hive", "teradata", "exasol")  # naive UTC wall-clock, unchanged
# 0.1.4 exact spellings: (value, databricks/spark text, spark2 text)
EDGE_TIMESTAMPS = [
    (
        dt.datetime(2025, 1, 31, 12),
        "TIMESTAMP '2025-01-31 12:00:00 UTC'",
        "CAST('2025-01-31 12:00:00Z' AS TIMESTAMP)",
    ),
    (
        dt.datetime(2025, 1, 31, 12, 0, 0, 1),
        "TIMESTAMP '2025-01-31 12:00:00.000001 UTC'",
        "CAST('2025-01-31 12:00:00.000001Z' AS TIMESTAMP)",
    ),
    (
        dt.datetime(1, 1, 1),
        "TIMESTAMP '0001-01-01 00:00:00 UTC'",
        "CAST('0001-01-01 00:00:00Z' AS TIMESTAMP)",
    ),
    (
        dt.datetime(9999, 12, 31, 23, 59, 59, 999999),
        "TIMESTAMP '9999-12-31 23:59:59.999999 UTC'",
        "CAST('9999-12-31 23:59:59.999999Z' AS TIMESTAMP)",
    ),
    (  # +05:00 converted to UTC
        AWARE,
        "TIMESTAMP '2019-12-31 19:00:00.000250 UTC'",
        "CAST('2019-12-31 19:00:00.000250Z' AS TIMESTAMP)",
    ),
    (  # -07:00 converted to UTC
        dt.datetime(2025, 6, 1, 1, 30, tzinfo=dt.timezone(dt.timedelta(hours=-7))),
        "TIMESTAMP '2025-06-01 08:30:00 UTC'",
        "CAST('2025-06-01 08:30:00Z' AS TIMESTAMP)",
    ),
]


@pytest.mark.parametrize("dialect", SESSION_LOCAL)
@pytest.mark.parametrize(("value", "text", "_spark2"), EDGE_TIMESTAMPS, ids=str)
def test_session_local_dialects_inline_an_explicit_utc_zone(
    dialect: str, value: dt.datetime, text: str, _spark2: str
) -> None:
    if dialect != "databricks":
        pytest.importorskip("sqlglot")
    assert literal(inline(value, "timestamp", dialect)) == text


@pytest.mark.parametrize(("value", "_spark3", "text"), EDGE_TIMESTAMPS, ids=str)
def test_spark2_inlines_a_cast_of_a_z_suffixed_string(
    value: dt.datetime, _spark3: str, text: str
) -> None:
    pytest.importorskip("sqlglot")
    assert literal(inline(value, "timestamp", "spark2")) == text


@pytest.mark.parametrize("dialect", SESSION_LOCAL)
def test_session_local_spelling_is_the_trino_spelling(dialect: str) -> None:
    pytest.importorskip("sqlglot")
    from factlang.sql.registry import literal_style_for

    trino = literal_style_for("trino").timestamp_literal
    assert trino == "TIMESTAMP '{date} {time} UTC'"
    assert literal_style_for(dialect).timestamp_literal == trino


@pytest.mark.parametrize(
    ("dialect", "zone"), [("databricks", " UTC"), ("spark", " UTC"), ("spark2", "Z")]
)
def test_sqlglot_keeps_the_utc_zone_in_the_literal_text(dialect: str, zone: str) -> None:
    sqlglot = pytest.importorskip("sqlglot")
    from sqlglot import exp

    for value in (AWARE, WHOLE, dt.datetime(1, 1, 1), dt.datetime(9999, 12, 31, 23, 59, 59)):
        sql = inline(value, "timestamp", dialect)
        text = quoted_text(literal(sql))
        assert text.endswith(zone) and not text.endswith(" Z"), sql
        node = sqlglot.parse_one(sql, read=dialect).expressions[0].this
        assert isinstance(node, exp.Cast) and node.this.this == text, (dialect, sql)
        assert node.to.is_type(*exp.DataType.TEMPORAL_TYPES), node.to
        regenerated = sqlglot.parse_one(sql, read=dialect).sql(dialect=dialect)
        again = sqlglot.parse_one(regenerated, read=dialect).expressions[0].this
        assert isinstance(again, exp.Cast) and again.this.this == text, regenerated


def test_only_the_three_spark_family_spellings_change_from_0_1_3() -> None:
    pytest.importorskip("sqlglot")
    from factlang.sql.registry import literal_style_for

    assert literal_style_for("spark2").timestamp_literal == "CAST('{date} {time}Z' AS TIMESTAMP)"
    for dialect in (*ZONE_LESS, "oracle"):
        assert literal_style_for(dialect).timestamp_literal == "TIMESTAMP '{date} {time}'"
    # the shared spelling is internal: no new public name on the trino profile module
    import factlang.sql.profiles.trino as trino_module

    assert "UTC_TIMESTAMP_LITERAL" not in vars(trino_module)
    assert all("TIMESTAMP" not in name for name in trino_module.__all__)


@pytest.mark.parametrize("dialect", ZONE_LESS)
def test_zone_less_dialects_keep_the_naive_utc_spelling(dialect: str) -> None:
    pytest.importorskip("sqlglot")
    from factlang.sql.registry import literal_style_for

    assert literal_style_for(dialect).timestamp_literal == "TIMESTAMP '{date} {time}'"
    assert literal(inline(AWARE, "timestamp", dialect)) == NAIVE_FORM[0]
    assert literal(inline(dt.datetime(1, 1, 1), "timestamp", dialect)) == (
        "TIMESTAMP '0001-01-01 00:00:00'"
    )


def test_the_ansi_timestamp_form_executes_in_duckdb_as_the_utc_instant() -> None:
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    con.execute("SET TimeZone = 'UTC'")
    for value, expected in (
        (AWARE, dt.datetime(2019, 12, 31, 19, 0, 0, 250)),
        (WHOLE, dt.datetime(2025, 1, 31, 12)),
    ):
        sql = inline(value, "timestamp", "duckdb")
        assert literal(sql) in ANSI_FORM
        (got,) = con.execute(f"SELECT CAST(({literal(sql)}) AS TIMESTAMP)").fetchone()
        assert got == expected


def test_the_ansi_and_other_native_spellings_are_unchanged() -> None:
    assert ANSI_STYLE.timestamp_literal == "TIMESTAMP '{date}T{time}+00:00'"
    for dialect in ("duckdb", "postgres", "snowflake", "bigquery", "mysql", "redshift"):
        assert literal(inline(AWARE, "timestamp", dialect)) == ANSI_FORM[0], dialect


def test_dates_are_unchanged_in_the_bridge_table_dialects() -> None:
    pytest.importorskip("sqlglot")
    for dialect in TIMESTAMP_SPELLINGS:
        assert literal(inline(dt.date(2025, 1, 31), "date", dialect)) == "DATE '2025-01-31'"


def test_the_bridge_table_never_decides_quote_doubling() -> None:
    pytest.importorskip("sqlglot")
    from factlang.sql import sqlglot_bridge as bridge
    from factlang.sql.registry import literal_style_for

    assert set(bridge.BRIDGE_LITERAL_STYLES) == {
        "presto",
        "athena",
        "hive",
        "spark",
        "spark2",
        "teradata",
        "exasol",
    }
    for name in bridge.BRIDGE_LITERAL_STYLES:
        style = literal_style_for(name)
        assert style.doubled_quote_escape is (bridge.lexer_doubles_quote(name) is True), name
    # the table's own flag agrees with the lexer where it has no '' form
    for name in ("hive", "spark", "spark2"):
        assert bridge.BRIDGE_LITERAL_STYLES[name].doubled_quote_escape is False, name
    # spark/hive lexers have no '' form: still refused there, still doubled on presto/teradata
    for name in ("hive", "spark", "spark2"):
        with pytest.raises(RenderError, match=FL507):
            probe("it's", "string", name).inline()
    for name in ("presto", "athena", "teradata", "exasol"):
        assert literal(probe("it's", "string", name).inline()) == "'it''s'"


def test_bridge_table_is_consulted_before_the_ancestor_walk() -> None:
    pytest.importorskip("sqlglot")
    from factlang.sql.registry import literal_style_for

    # databricks is a sqlglot Spark subclass with a native profile; spark itself is bridged,
    # and hive (Spark's sqlglot parent) keeps its own naive entry
    assert literal_style_for("spark").timestamp_literal == "TIMESTAMP '{date} {time} UTC'"
    assert literal_style_for("hive").timestamp_literal == "TIMESTAMP '{date} {time}'"
    # dune is a Trino subclass without a table entry: it inherits the native trino spelling
    assert literal_style_for("dune").timestamp_literal == "TIMESTAMP '{date} {time} UTC'"
    # fabric still walks to T-SQL
    assert literal_style_for("fabric").timestamp_literal == "CAST('{date} {time}' AS DATETIME2)"


def test_a_table_entry_wins_over_the_ancestor_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    # pin the order with an entry that differs from what the walk would give: dune walks to
    # the native trino profile, so a table entry for dune must be what literal_style returns
    pytest.importorskip("sqlglot")
    import dataclasses
    from types import MappingProxyType

    from factlang.sql import sqlglot_bridge as bridge
    from factlang.sql.registry import literal_style_for

    walked = literal_style_for("dune")
    marker = "TIMESTAMP '{date} {time} (table)'"
    entry = dataclasses.replace(walked, timestamp_literal=marker, true_literal="1")
    table = {**bridge.BRIDGE_LITERAL_STYLES, "dune": entry}
    monkeypatch.setattr(bridge, "BRIDGE_LITERAL_STYLES", MappingProxyType(table))
    style = literal_style_for("dune")
    assert (style.timestamp_literal, style.true_literal) == (marker, "1")
    assert style.doubled_quote_escape is walked.doubled_quote_escape  # still the lexer's
    monkeypatch.undo()
    assert literal_style_for("dune") == walked


# -- I2 booleans ------------------------------------------------------------------------------


def test_teradata_inlines_booleans_as_1_and_0() -> None:
    sqlglot = pytest.importorskip("sqlglot")
    from sqlglot import exp

    for value, text in ((True, "1"), (False, "0")):
        sql = inline(value, "boolean", "teradata")
        assert literal(sql) == text
        node = sqlglot.parse_one(sql, read="teradata").expressions[0].this
        assert isinstance(node, exp.Literal) and not node.is_string and node.this == text
    assert literal(inline(True, "boolean", "exasol")) == "TRUE"
    assert literal(inline(True, "boolean", "presto")) == "TRUE"


# -- I3 precision caps --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "precision"),
    [
        (Decimal("1E+50"), 51),
        (Decimal("1E-50"), 50),
        (Decimal("9" * 39), 39),
        (Decimal("0." + "1" * 39), 39),
        (Decimal("1" * 20 + "." + "1" * 19), 39),
        (Decimal("-1E+38"), 39),
    ],
    ids=str,
)
def test_a_decimal_past_38_digits_of_precision_is_fl507(value: Decimal, precision: int) -> None:
    with pytest.raises(RenderError, match=rf"{FL507}.*precision {precision}.*38"):
        inline(value, "decimal")
    assert display_sql(probe(value, "decimal")) == "SELECT {{p1}} AS x"


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (Decimal("9" * 38), "9" * 38),
        (Decimal("0." + "1" * 38), "0." + "1" * 38),
        (Decimal("1E+37"), "1" + "0" * 37),
        (Decimal("1E-38"), "0." + "0" * 37 + "1"),
        (Decimal("-0"), "-0"),
        (Decimal("0E+50"), "0"),
        (Decimal("2.50"), "2.50"),
    ],
    ids=str,
)
def test_a_decimal_within_38_digits_inlines(value: Decimal, text: str) -> None:
    assert literal(inline(value, "decimal")) == text


def test_an_int_outside_signed_64_bit_is_fl507() -> None:
    lo, hi = -(2**63), 2**63 - 1
    assert literal(inline(hi, "integer")) == str(hi)
    assert literal(inline(lo, "integer")) == str(lo)
    for value in (hi + 1, lo - 1, 10**30, -(10**30)):
        with pytest.raises(RenderError, match=rf"{FL507}.*signed 64-bit"):
            inline(value, "integer")
    with pytest.raises(RenderError, match=FL507):
        inline(2**63, "decimal")  # an int in a decimal family is still an int


def test_finite_floats_always_fit_17_significant_digits() -> None:
    for value in (math.pi, 5e-324, 1.7976931348623157e308, -0.1, 0.0, 1e22, 123456789.12345679):
        text = literal(inline(value, "float"))
        assert float(text) == value


def test_bound_parameters_are_unaffected_by_the_caps() -> None:
    p = probe(Decimal("1E+50"), "decimal")
    sql, params = p.bind("qmark")
    assert sql == "SELECT ? AS x" and params == (Decimal("1E+50"),)
    big = probe(2**70, "integer")
    assert big.bind("qmark") == ("SELECT ? AS x", (2**70,))


def test_a_custom_style_still_applies_the_caps() -> None:
    style = LiteralStyle(timestamp_literal="TIMESTAMP '{date} {time}'")
    with pytest.raises(RenderError, match=FL507):
        probe(Decimal("1E+50"), "decimal").inline(style=style)
