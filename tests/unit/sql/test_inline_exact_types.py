"""Literal inlining trusts no value's own methods (post-0.1.1 security review).

Regression tests built from the review's probe scripts: exact types only (every subclass is
refused), base-type rendering, ``FL507`` for every value-dependent refusal, the quote form
chosen by the resolved dialect's string-escape rules, and per-dialect literal spellings
(T-SQL ``N'…'``, ``1``/``0`` booleans on T-SQL and Oracle, timestamps those engines accept).
"""

from __future__ import annotations

import datetime as dt
import enum
import time
from decimal import Decimal

import pytest

import factlang as fl
from factlang.engine import display_sql, string_quoter
from factlang.ir import Arity, ColumnAggregate, Metric
from factlang.semantics.filters import Param
from factlang.semantics.refs import Ident, TableRef
from factlang.sql import get_profile, native_dialects
from factlang.sql.fragment import RenderedFragment
from factlang.sql.probe import RenderedProbe, RenderError

sqlglot = pytest.importorskip("sqlglot")
from sqlglot import exp  # noqa: E402

from factlang.sql.registry import SQLGLOT_NAMES, literal_style_for  # noqa: E402

PLAN = ColumnAggregate(TableRef(None, None, Ident("orders")), None, (Metric.ROW_COUNT,))
NATIVE = native_dialects()
BRIDGED_NO_DOUBLING = ["spark", "spark2", "hive"]
FL507 = r"^FL507 cannot inline"


def probe(value: object, family: str, dialect: str = "duckdb") -> RenderedProbe:
    return RenderedProbe(
        "SELECT {{p1}} AS x", (Param("p1", value, family),), Arity.SINGLE_ROW, PLAN, dialect, "v"
    )


def inline(value: object, family: str, dialect: str = "duckdb") -> str:
    return probe(value, family, dialect).inline(string_quoter(dialect))


def literal_node(sql: str, dialect: str) -> exp.Expression:
    tree = sqlglot.parse_one(sql, read=SQLGLOT_NAMES.get(dialect, dialect))
    node: exp.Expression = tree.expressions[0].this
    return node


# -- exact types only ------------------------------------------------------------------------


class HostileFloat(float):
    def __repr__(self) -> str:
        return "1 OR 1=1"


class HostileInt(int):
    def __str__(self) -> str:
        return "1 OR 1=1"

    def __repr__(self) -> str:
        return "1 OR 1=1"


class MixinEnum(int, enum.Enum):
    A = 1


class AnIntEnum(enum.IntEnum):
    A = 1


class HostileDecimal(Decimal):
    def __format__(self, spec: str) -> str:
        return "1 OR 1=1"


class HostileStr(str):
    def __str__(self) -> str:
        return self

    def replace(self, *args: object, **kwargs: object) -> str:  # type: ignore[override]
        return str.__str__(self)

    def __contains__(self, x: object) -> bool:
        return False


class HostileDate(dt.date):
    def isoformat(self) -> str:
        return HostileStr("2020-01-01' OR 1=1 --")


class HostileDatetime(dt.datetime):
    def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
        return "2020-01-01' OR 1=1 --"


class LyingMeta(type):
    def __eq__(cls, other: object) -> bool:
        return True

    def __hash__(cls) -> int:
        return hash(int)


class LyingInt(int, metaclass=LyingMeta):
    def __repr__(self) -> str:
        return "1 OR 1=1"


SUBCLASSED: list[tuple[object, str]] = [
    (HostileFloat(1.5), "float"),
    (HostileInt(1), "integer"),
    (MixinEnum.A, "integer"),
    (AnIntEnum.A, "integer"),
    (HostileDecimal("1"), "decimal"),
    (HostileStr("x' OR 1=1 --"), "string"),
    (HostileDate(2020, 1, 1), "date"),
    (HostileDate(2020, 1, 1), "string"),
    (HostileDatetime(2020, 1, 1), "timestamp"),
    (LyingInt(1), "integer"),
]


@pytest.mark.parametrize("dialect", NATIVE)
@pytest.mark.parametrize(("value", "family"), SUBCLASSED, ids=lambda v: type(v).__name__)
def test_subclasses_of_scalar_types_are_refused(dialect: str, value: object, family: str) -> None:
    with pytest.raises(RenderError, match=FL507) as info:
        inline(value, family, dialect)
    assert "OR 1=1" not in str(info.value)


def test_numpy_scalars_are_refused() -> None:
    np = pytest.importorskip("numpy")
    for value, family in [
        (np.float64(1.5), "float"),
        (np.int64(3), "integer"),
        (np.bool_(True), "boolean"),
        (np.str_("it's"), "string"),
    ]:
        with pytest.raises(RenderError, match=FL507):
            inline(value, family)


class HostileTz(dt.tzinfo):
    def utcoffset(self, d: dt.datetime | None) -> dt.timedelta:
        raise RuntimeError("1 OR 1=1")

    def dst(self, d: dt.datetime | None) -> dt.timedelta:
        return dt.timedelta(0)


def test_a_datetime_with_a_foreign_tzinfo_is_refused() -> None:
    with pytest.raises(RenderError, match=FL507):
        inline(dt.datetime(2020, 1, 1, tzinfo=HostileTz()), "timestamp")


def test_zoneinfo_and_fixed_offsets_are_accepted() -> None:
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        berlin = zoneinfo.ZoneInfo("Europe/Berlin")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no tz database")
    out = inline(dt.datetime(2020, 1, 1, 1, 0, tzinfo=berlin), "timestamp")
    assert out == "SELECT TIMESTAMP '2020-01-01T00:00:00+00:00' AS x"


# -- type/family combinations ------------------------------------------------------------------

MISMATCHED: list[tuple[object, str]] = [
    (dt.datetime(2020, 1, 1, 10, 0), "date"),
    (dt.date(2020, 1, 1), "timestamp"),
    (True, "integer"),
    (False, "string"),
    (7, "string"),
    (7, "boolean"),
    ("2020-01-01", "date"),
    ("1", "integer"),
    (1.5, "string"),
]


@pytest.mark.parametrize(("value", "family"), MISMATCHED, ids=repr)
def test_a_value_must_match_its_family(value: object, family: str) -> None:
    with pytest.raises(RenderError, match=FL507):
        inline(value, family)


def test_numbers_inline_in_any_numeric_family() -> None:
    assert inline(3, "decimal") == "SELECT 3 AS x"
    assert inline(1.5, "decimal") == "SELECT 1.5 AS x"
    assert inline(Decimal("2.5"), "float") == "SELECT 2.5 AS x"


# -- numbers -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value", [Decimal("NaN"), Decimal("sNaN"), Decimal("-Infinity"), float("nan")], ids=str
)
def test_non_finite_numbers_are_fl507(value: object) -> None:
    family = "float" if isinstance(value, float) else "decimal"
    with pytest.raises(RenderError, match=FL507):
        inline(value, family)


def test_extreme_decimals_are_refused_fast_and_ordinary_ones_render() -> None:
    start = time.monotonic()
    for value in [Decimal("1E+100000000"), Decimal("1E-100000000"), Decimal("9" * 5000)]:
        with pytest.raises(RenderError, match=FL507):
            inline(value, "decimal")
    assert time.monotonic() - start < 1.0
    assert inline(Decimal("-0"), "decimal") == "SELECT -0 AS x"
    # 0.1.3 (I3): past DECIMAL's 38 digits of precision is FL507; within, the plain form
    for value in [Decimal("1E+50"), Decimal("1E-50")]:
        with pytest.raises(RenderError, match=FL507):
            inline(value, "decimal")
    assert inline(Decimal("1E+37"), "decimal") == "SELECT 1" + "0" * 37 + " AS x"
    assert inline(Decimal("1E-38"), "decimal") == "SELECT 0." + "0" * 37 + "1 AS x"


def test_an_integer_past_the_digit_limit_is_fl507_not_value_error() -> None:
    with pytest.raises(RenderError, match=FL507):
        inline(10**5000, "integer")
    with pytest.raises(RenderError, match=FL507):  # 0.1.3: past signed 64-bit (I3)
        inline(10**4000, "integer")
    assert inline(2**63 - 1, "integer") == f"SELECT {2**63 - 1} AS x"
    assert display_sql(probe(10**5000, "integer")) == "SELECT {{p1}} AS x"


# -- strings -----------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["a\ud800", "\udfff"])
def test_lone_surrogates_are_refused(text: str) -> None:
    with pytest.raises(RenderError, match=FL507):
        inline(text, "string")


@pytest.mark.parametrize("dialect", NATIVE)
@pytest.mark.parametrize("ch", ["\u2019", "\u02bc", "\uff07", "\u2018", "`", "\u00b4"])
def test_quote_lookalikes_round_trip_as_themselves(dialect: str, ch: str) -> None:
    text = f"a{ch} OR 1=1 --"
    node = literal_node(inline(text, "string", dialect), dialect)
    assert isinstance(node, (exp.Literal, exp.National)), node
    assert node.this == text


def test_tsql_strings_inline_as_national_literals() -> None:
    sql = inline("it's", "string", "tsql")
    assert sql == "SELECT N'it''s' AS x"
    node = literal_node(sql, "tsql")
    assert isinstance(node, exp.National) and node.this == "it's"
    assert inline("plain", "string", "duckdb") == "SELECT 'plain' AS x"


# -- quote form follows the resolved dialect -------------------------------------------------


def test_native_doubled_quote_flag() -> None:
    for name in NATIVE:
        profile = get_profile(name)
        assert profile is not None
        assert profile.doubled_quote_escape is (name not in ("bigquery", "databricks")), name


@pytest.mark.parametrize(
    "dialect",
    [
        "bigquery",
        "databricks",
        *BRIDGED_NO_DOUBLING,
        " bigquery",
        "bigquery, normalization_strategy = lowercase",
        "BigQuery",
        "no-such-dialect",
    ],
)
def test_a_quote_is_refused_where_the_lexer_has_no_doubled_quote(dialect: str) -> None:
    with pytest.raises(RenderError, match=r"^FL507"):
        string_quoter(dialect)("it's")
    with pytest.raises(RenderError, match=FL507):
        probe("it's", "string", dialect).inline()
    # a custom quoter cannot bypass it either
    with pytest.raises(RenderError, match=FL507):
        probe("it's", "string", dialect).inline(lambda s: "'" + s.replace("'", "''") + "'")
    assert string_quoter(dialect)("plain") == "'plain'"


@pytest.mark.parametrize("dialect", ["presto", "trino", "postgres", "athena"])
def test_a_quote_is_doubled_where_the_lexer_reads_it(dialect: str) -> None:
    assert string_quoter(dialect)("it's") == "'it''s'"


def test_bridged_dialects_inherit_the_nearest_native_spellings() -> None:
    fabric = literal_style_for("fabric")
    assert fabric.string_prefix == "N" and fabric.true_literal == "1"
    assert literal_style_for("materialize") == literal_style_for("postgres")
    assert literal_style_for("spark").doubled_quote_escape is False
    assert literal_style_for("spark").true_literal == "TRUE"


# -- booleans, dates and timestamps per dialect -------------------------------------------------


@pytest.mark.parametrize("dialect", NATIVE)
def test_booleans_parse_as_the_dialects_boolean(dialect: str) -> None:
    for value in (True, False):
        sql = inline(value, "boolean", dialect)
        node = literal_node(sql, dialect)
        if dialect in ("tsql", "oracle"):
            assert isinstance(node, exp.Literal) and not node.is_string
            assert node.this == ("1" if value else "0")
        else:
            assert isinstance(node, exp.Boolean) and node.this is value, (dialect, sql)


AWARE = dt.datetime(2020, 1, 1, 0, 0, 0, 250, tzinfo=dt.timezone(dt.timedelta(hours=5)))


@pytest.mark.parametrize("dialect", NATIVE)
def test_timestamps_are_utc_and_parse_in_every_native_dialect(dialect: str) -> None:
    sql = inline(AWARE, "timestamp", dialect)
    assert "2019-12-31" in sql and "19:00:00.000250" in sql, sql
    node = literal_node(sql, dialect)
    if dialect == "tsql":
        assert sql == "SELECT CAST('2019-12-31 19:00:00.000250' AS DATETIME2) AS x"
        assert isinstance(node, exp.Cast) and node.to.sql("tsql") == "DATETIME2"
    elif dialect in ("oracle", "databricks"):
        assert sql == "SELECT TIMESTAMP '2019-12-31 19:00:00.000250' AS x"
    elif dialect == "trino":
        assert sql == "SELECT TIMESTAMP '2019-12-31 19:00:00.000250 UTC' AS x"
    else:
        assert sql == "SELECT TIMESTAMP '2019-12-31T19:00:00.000250+00:00' AS x"
    naive = inline(dt.datetime(2025, 1, 31, 12), "timestamp", dialect)
    assert "2025-01-31" in naive and "12:00:00" in naive


@pytest.mark.parametrize("dialect", NATIVE)
def test_dates_parse_in_every_native_dialect(dialect: str) -> None:
    sql = inline(dt.date(5, 2, 3), "date", dialect)
    assert "0005-02-03" in sql
    literal_node(sql, dialect)
    if dialect == "tsql":
        assert sql == "SELECT CAST('0005-02-03' AS DATE) AS x"
    else:
        assert sql == "SELECT DATE '0005-02-03' AS x"


def test_a_timestamp_that_overflows_in_utc_is_refused() -> None:
    tz = dt.timezone(dt.timedelta(hours=-5))
    with pytest.raises(RenderError, match=FL507):
        inline(dt.datetime(9999, 12, 31, 23, 0, tzinfo=tz), "timestamp")


# -- display_sql ---------------------------------------------------------------------------------


def test_display_sql_falls_back_on_fl507_and_on_conversion_errors() -> None:
    assert display_sql(probe(HostileInt(1), "integer")) == "SELECT {{p1}} AS x"

    def broken(text: str) -> str:
        raise UnicodeError("boom")

    assert display_sql(probe("x", "string"), broken) == "SELECT {{p1}} AS x"
    with pytest.raises(RenderError, match=r"^FL507"):
        probe("x", "string").inline(broken)


def test_display_sql_lets_a_genuine_render_bug_propagate() -> None:
    def buggy(text: str) -> str:
        raise RenderError("FL500 the quoter is broken")

    with pytest.raises(RenderError, match=r"^FL500"):
        display_sql(probe("x", "string"), buggy)


def test_fragments_follow_the_same_rules() -> None:
    frag = RenderedFragment("s = {{p1}}", (Param("p1", "it's", "string"),), "tsql", "verified")
    assert frag.inline() == "s = N'it''s'"
    frag = RenderedFragment("s = {{p1}}", (Param("p1", MixinEnum.A, "integer"),), "duckdb", "v")
    with pytest.raises(RenderError, match=FL507):
        frag.inline()
    assert display_sql(frag) == "s = {{p1}}"


@pytest.mark.parametrize(
    ("claim", "dialect"),
    [
        ("COUNT orders VALUE == 0 WHERE amount = " + "9" * 999, "duckdb"),
        ("COUNT orders VALUE == 0 WHERE amount = " + "9" * 999 + ".5", "duckdb"),
        ("COUNT orders VALUE == 0 WHERE status = 'it''s'", "spark"),
        ("COUNT orders VALUE == 0 WHERE status = 'it''s'", "hive"),
        ("COUNT orders VALUE == 0 WHERE status = 'it''s'", "spark2"),
        ("COUNT orders VALUE == 0 WHERE d = TIMESTAMP '2020-01-01T00:00:00+05:00'", "tsql"),
    ],
)
def test_display_sql_of_compiled_claims_never_raises(claim: str, dialect: str) -> None:
    for p in fl.compile(claim, dialect=dialect):
        text = display_sql(p, string_quoter(dialect))
        assert text.startswith("SELECT")
        if "'it''s'" in claim:
            assert "{{p1}}" in text


# -- 0.1.2 fold-ins (pre-release review) ----------------------------------------------------


def test_a_custom_profile_cannot_claim_doubling_its_lexer_lacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A native profile whose name resolves to a sqlglot dialect gets doubling only if both
    the profile AND that dialect's tokenizer read ``''`` as a quote (fail closed)."""
    import dataclasses

    from factlang.sql import registry

    pg = get_profile("postgres")
    assert pg is not None and pg.doubled_quote_escape is True
    monkeypatch.setitem(registry._PROFILES, "hive", dataclasses.replace(pg, name="hive"))
    assert literal_style_for("hive").doubled_quote_escape is False
    with pytest.raises(RenderError, match=r"^FL507"):
        string_quoter("hive")("it's")
    with pytest.raises(RenderError, match=FL507):
        probe("it's", "string", "hive").inline()
    assert display_sql(probe("it's", "string", "hive")) == "SELECT {{p1}} AS x"
    # a name sqlglot does not know keeps the profile's own (explicit) choice
    monkeypatch.setitem(registry._PROFILES, "mypg", dataclasses.replace(pg, name="mypg"))
    assert string_quoter("mypg")("it's") == "'it''s'"


def test_doubled_quote_escape_defaults_to_false() -> None:
    import dataclasses

    from factlang.sql.profile import DialectProfile

    field = {f.name: f for f in dataclasses.fields(DialectProfile)}["doubled_quote_escape"]
    assert field.default is False


LINE_TERMINATORS = ["\r", "\n", "\r\n", "\x85", "\u2028", "\u2029"]


@pytest.mark.parametrize("dialect", [*NATIVE, "spark", "no-such-dialect"])
@pytest.mark.parametrize("ch", LINE_TERMINATORS)
def test_line_terminators_are_never_inlined(dialect: str, ch: str) -> None:
    text = f"a{ch}b"
    with pytest.raises(RenderError, match=FL507):
        probe(text, "string", dialect).inline()
    with pytest.raises(RenderError, match=r"^FL507"):
        string_quoter(dialect)(text)
    # a host quote function cannot bypass it
    with pytest.raises(RenderError, match=FL507):
        probe(text, "string", dialect).inline(lambda s: "'" + s + "'")
    assert display_sql(probe(text, "string", dialect)) == "SELECT {{p1}} AS x"


def test_a_blank_line_statement_break_falls_back_to_the_template() -> None:
    hostile = "x\n\nDROP TABLE t;"
    for dialect in ("oracle", "postgres", "duckdb"):
        with pytest.raises(RenderError, match=FL507):
            probe(hostile, "string", dialect).inline()
        assert "DROP" not in display_sql(probe(hostile, "string", dialect))
    frag = RenderedFragment("s = {{p1}}", (Param("p1", hostile, "string"),), "oracle", "native")
    with pytest.raises(RenderError, match=FL507):
        frag.inline()
    assert display_sql(frag) == "s = {{p1}}"
    # the claim language itself has no raw line break inside a string literal
    with pytest.raises(ValueError, match="FL101"):
        fl.compile("COUNT orders VALUE == 0 WHERE status = 'x\n\nDROP TABLE t;'", dialect="oracle")
