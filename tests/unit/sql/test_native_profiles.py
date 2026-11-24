"""Task 15: the nine remaining native profiles pin their dialect spellings (spec 4.3, 8.3-8.5,
18 R7, 19 B2). The shared behaviour is covered by tests/conformance/test_dialects.py."""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from factlang.ir import (
    ColumnAggregate,
    KeyOverlap,
    Metric,
    PatternCoverage,
    SampleSpec,
    ValueMembership,
)
from factlang.semantics.refs import ColumnRef, Ident, TableRef
from factlang.sql import RenderError, get_profile, native_dialects, render
from factlang.sql.lower import Lowerer
from factlang.sql.profile import DialectProfile

ORDERS = TableRef(None, None, Ident("orders"))
SHOP_ORDERS = TableRef(None, Ident("shop"), Ident("orders"))
ID, STATUS, AMOUNT = Ident("id"), Ident("status"), Ident("amount")

NEW = ("bigquery", "snowflake", "redshift", "databricks", "clickhouse", "tsql", "mysql",
       "oracle", "trino")  # fmt: skip


def prof(name: str) -> DialectProfile:
    p = get_profile(name)
    assert p is not None, f"{name} is not registered"
    return p


def sql(plan: object, name: str) -> str:
    return render(plan, prof(name)).sql_template  # type: ignore[arg-type]


def test_all_eleven_native_profiles_are_registered() -> None:
    assert set(native_dialects()) == {*NEW, "duckdb", "postgres"}


# spec 8.5 (+ R7: trino verified)
TIERS = {
    "bigquery": "native",
    "snowflake": "native",
    "redshift": "native",
    "databricks": "native",
    "oracle": "native",
    "clickhouse": "verified",
    "tsql": "verified",
    "mysql": "verified",
    "trino": "verified",
}

# spec 4.3 slot table
MAX_PARTS = {
    "bigquery": 3,
    "snowflake": 3,
    "redshift": 3,
    "databricks": 3,
    "tsql": 3,
    "trino": 3,
    "mysql": 2,
    "clickhouse": 2,
    "oracle": 2,
}

# spec 8.4: Snowflake/Oracle upper; Postgres/Redshift lower; Trino lower (R7); others preserve
CASES = {
    "snowflake": "upper",
    "oracle": "upper",
    "redshift": "lower",
    "trino": "lower",
    "bigquery": "preserve",
    "databricks": "preserve",
    "clickhouse": "preserve",
    "tsql": "preserve",
    "mysql": "preserve",
}

QUOTES = {
    "bigquery": "`",
    "databricks": "`",
    "mysql": "`",
    "tsql": '"',
    "snowflake": '"',
    "redshift": '"',
    "oracle": '"',
    "trino": '"',
    "clickhouse": '"',
}

NOW = {
    "bigquery": "CURRENT_TIMESTAMP()",
    "tsql": "SYSUTCDATETIME()",
    "oracle": "SYS_EXTRACT_UTC(SYSTIMESTAMP)",
    "mysql": "UTC_TIMESTAMP()",
    "clickhouse": "now()",
    "databricks": "current_timestamp()",
    "redshift": "GETDATE()",
    "trino": "current_timestamp",
    "snowflake": "CURRENT_TIMESTAMP()",
}

# spec 19 B2 keyhash ruling, verbatim per profile (h folded, never ABS)
KEYHASH = {
    "bigquery": "MOD(MOD(FARM_FINGERPRINT({s}), 1000000) + 1000000, 1000000)",
    "snowflake": "MOD(MOD(HASH({s}), 1000000) + 1000000, 1000000)",
    "databricks": "MOD(MOD(xxhash64({s}), 1000000) + 1000000, 1000000)",
    "clickhouse": "(cityHash64({s}) % 1000000)",
    "redshift": "MOD(STRTOL(SUBSTRING(MD5({s}), 1, 15), 16), 1000000)",
    "mysql": "MOD(CAST(CONV(SUBSTRING(MD5({s}), 1, 15), 16, 10) AS UNSIGNED), 1000000)",
    "tsql": (
        "((CONVERT(BIGINT, SUBSTRING(HASHBYTES('MD5', CAST({s} AS NVARCHAR(4000))), 1, 8))"
        " % 1000000 + 1000000) % 1000000)"
    ),
    "oracle": "MOD(ORA_HASH({s}, 4294967295, 0), 1000000)",
    "trino": "MOD(MOD(from_big_endian_64(xxhash64(to_utf8({s}))), 1000000) + 1000000, 1000000)",
}

# only methods the dialect can repeat with a seed are declared; everything else is FL506
SAMPLE_STYLES: dict[str, dict[str, str | None]] = {
    "bigquery": {"system": None, "bernoulli": None},
    "snowflake": {
        "system": "SAMPLE SYSTEM ({percent}) SEED ({seed})",
        "bernoulli": "SAMPLE BERNOULLI ({percent}) SEED ({seed})",
    },
    "redshift": {"system": None, "bernoulli": None},
    "databricks": {
        "system": None,
        "bernoulli": "TABLESAMPLE ({percent} PERCENT) REPEATABLE ({seed})",
    },
    "clickhouse": {"system": None, "bernoulli": None},
    "tsql": {
        "system": "TABLESAMPLE SYSTEM ({percent} PERCENT) REPEATABLE ({seed})",
        "bernoulli": None,
    },
    "mysql": {"system": None, "bernoulli": None},
    "oracle": {
        "system": "SAMPLE BLOCK ({percent}) SEED ({seed})",
        "bernoulli": "SAMPLE ({percent}) SEED ({seed})",
    },
    "trino": {"system": None, "bernoulli": None},
}


@pytest.mark.parametrize("name", NEW)
def test_profile_facts(name: str) -> None:
    p = prof(name)
    assert p.tier == TIERS[name]
    assert p.max_table_parts == MAX_PARTS[name]
    assert p.unquoted_case == CASES[name]
    assert p.quote_char == QUOTES[name]
    assert p.now_sql == NOW[name]
    assert p.keyhash_expr == KEYHASH[name]
    assert "ABS" not in p.keyhash_expr.upper()
    assert dict(p.sample_styles) == SAMPLE_STYLES[name]
    assert sql(ColumnAggregate(ORDERS, None, (Metric.NOW,)), name).startswith(
        f"SELECT {NOW[name]} AS server_now FROM orders"
    )


def test_bigquery_param_types_are_the_driver_names() -> None:
    assert dict(prof("bigquery").param_types) == {
        "integer": "INT64",
        "decimal": "NUMERIC",
        "float": "FLOAT64",
        "string": "STRING",
        "boolean": "BOOL",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
    }


def test_trino_param_types() -> None:
    assert dict(prof("trino").param_types) == {
        "integer": "bigint",
        "decimal": "decimal",
        "float": "double",
        "string": "varchar",
        "boolean": "boolean",
        "date": "date",
        "timestamp": "timestamp(6) with time zone",
    }


COUNT_IF = {
    "bigquery": "COALESCE(COUNTIF(status = {{p1}}), 0) AS matching",
    "snowflake": "COALESCE(COUNT_IF(status = {{p1}}), 0)",
    "databricks": "COALESCE(COUNT_IF(status = {{p1}}), 0)",
    "trino": "COALESCE(COUNT_IF(status = {{p1}}), 0)",
    "clickhouse": "countIf(status = {{p1}})",
    "redshift": "COALESCE(SUM(CASE WHEN status = CAST({{p1}} AS VARCHAR(65535)) THEN 1 ELSE 0",
    "mysql": "COALESCE(SUM(CASE WHEN status = {{p1}} THEN 1 ELSE 0 END), 0)",
    "oracle": "COALESCE(SUM(CASE WHEN status = {{p1}} THEN 1 ELSE 0 END), 0)",
    "tsql": "COUNT_BIG(CASE WHEN status = {{p1}} THEN 1 END)",
}


@pytest.mark.parametrize("name", NEW)
def test_conditional_count_spelling(name: str) -> None:
    from factlang.sql import ast as A
    from factlang.sql.printer import Printer

    lw = Lowerer()
    cond = A.Compare("=", A.Column(STATUS), lw.param("x", "string"))
    text = Printer(prof(name)).expression(A.CountIf(cond))
    expected = COUNT_IF[name].replace(" AS matching", "")
    assert text.startswith(expected)


LIMITS = {
    "bigquery": " LIMIT 5)",
    "snowflake": " LIMIT 5)",
    "databricks": " LIMIT 5)",
    "trino": " LIMIT 5)",
    "clickhouse": " LIMIT 5)",
    "redshift": " LIMIT 5)",
    "mysql": " LIMIT 5)",
    "oracle": " FETCH FIRST 5 ROWS ONLY)",
    "tsql": "SELECT TOP 5 ",
}


@pytest.mark.parametrize("name", NEW)
def test_ranked_values_limit_spelling(name: str) -> None:
    text = sql(ValueMembership(ORDERS, STATUS, ("paid",), report_limit=5), name)
    assert LIMITS[name] in text
    if name in ("redshift", "oracle", "clickhouse", "mysql", "databricks", "tsql"):
        # the ranked branch is a derived table, never a parenthesised UNION branch
        assert re.search(r"UNION ALL SELECT \* FROM \(SELECT", text)


SPLIT = {
    "bigquery": "SPLIT(CAST(status AS STRING), {{p1}})[SAFE_OFFSET(0)]",
    "snowflake": "SPLIT_PART(CAST(status AS VARCHAR), {{p1}}, 1)",
    "databricks": "SPLIT_PART(CAST(status AS STRING), {{p1}}, 1)",
    "trino": "SPLIT_PART(CAST(status AS varchar), {{p1}}, 1)",
    "redshift": "SPLIT_PART(CAST(status AS VARCHAR(65535)), CAST({{p1}} AS VARCHAR(65535)), 1)",
    "clickhouse": "splitByString({{p1}}, CAST(status AS Nullable(String)))[1]",
    "mysql": "SUBSTRING_INDEX(CAST(status AS CHAR), {{p1}}, 1)",
    "tsql": (
        "LEFT(CAST(status AS NVARCHAR(4000)), "
        "CHARINDEX({{p1}}, CAST(status AS NVARCHAR(4000)) + {{p1}}) - 1)"
    ),
    "oracle": (
        "SUBSTR(CAST(status AS VARCHAR2(4000)), 1, "
        "INSTR(CAST(status AS VARCHAR2(4000)) || {{p1}}, {{p1}}) - 1)"
    ),
}


@pytest.mark.parametrize("name", NEW)
def test_split_prefix_spelling(name: str) -> None:
    text = sql(ValueMembership(ORDERS, STATUS, (), split=":"), name)
    assert f"TRIM({SPLIT[name]})" in text


def test_tsql_counts_are_count_big_and_avg_is_not_integer_division() -> None:
    text = sql(ColumnAggregate(ORDERS, AMOUNT, (Metric.ROW_COUNT, Metric.NON_NULL, Metric.AVG)),
               "tsql")  # fmt: skip
    assert "COUNT_BIG(*) AS row_count" in text and "COUNT_BIG(amount) AS non_null" in text
    assert "AVG(amount * 1.0) AS avg_value" in text
    assert not re.search(r"\bCOUNT\(", text)


def test_redshift_avg_is_not_integer_division() -> None:
    assert "AVG(amount * 1.0) AS avg_value" in sql(
        ColumnAggregate(ORDERS, AMOUNT, (Metric.AVG,)), "redshift"
    )


def test_tsql_keyhash_concatenates_with_plus() -> None:
    spec = SampleSpec("keyhash", Decimal("0.01"), seed=42, key=(ID,))
    text = sql(PatternCoverage(ORDERS, STATUS, "%a%", sample=spec), "tsql")
    assert "HASHBYTES('MD5', CAST(({{p1}} + '|' + CAST(id AS NVARCHAR(4000)))" in text


def test_mysql_keyhash_concatenates_with_concat() -> None:
    spec = SampleSpec("keyhash", Decimal("0.01"), seed=42, key=(ID,))
    text = sql(PatternCoverage(ORDERS, STATUS, "%a%", sample=spec), "mysql")
    assert "MD5(CONCAT({{p1}}, '|', CAST(id AS CHAR)))" in text
    assert "||" not in text


def test_clickhouse_left_join_uses_nulls_and_typed_nulls_are_bare() -> None:
    plan = KeyOverlap((ColumnRef(ORDERS, Ident("customer_id")),), (ColumnRef(SHOP_ORDERS, ID),))
    text = sql(plan, "clickhouse")
    assert text.count("SETTINGS join_use_nulls = 1)") == 1
    assert "LEFT JOIN" in text
    vm = sql(ValueMembership(ORDERS, STATUS, ("paid",)), "clickhouse")
    assert "NULL AS non_null" in vm and "CAST(NULL" not in vm


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("bigquery", "COUNT(`a\\`b\\\\c`)"),
        ("clickhouse", 'COUNT("a`b\\\\c")'),
        ("mysql", "COUNT(`a``b\\c`)"),
        ("databricks", "COUNT(`a``b\\c`)"),
    ],
)
def test_backslash_and_backtick_in_quoted_names(name: str, expected: str) -> None:
    text = sql(ColumnAggregate(ORDERS, Ident("a`b\\c", quoted=True), (Metric.NON_NULL,)), name)
    assert expected in text


def test_bigquery_quoted_table_parts_are_escaped() -> None:
    t = TableRef(Ident("my-proj", quoted=True), Ident("shop"), Ident("or`ders", quoted=True))
    text = sql(ColumnAggregate(t, None, (Metric.ROW_COUNT,)), "bigquery")
    assert "FROM `my-proj`.shop.`or\\`ders`" in text


def test_bigquery_metadata_reads_the_dataset_information_schema() -> None:
    from factlang.ir import MetadataQuery

    probe = render(MetadataQuery(SHOP_ORDERS, STATUS), prof("bigquery"))
    assert "FROM shop.INFORMATION_SCHEMA.COLUMNS WHERE table_name = {{cat1}}" in (
        probe.sql_template
    )
    assert [p.value for p in probe.params] == ["orders", "status"]
    bare = render(MetadataQuery(ORDERS), prof("bigquery")).sql_template
    assert "FROM INFORMATION_SCHEMA.COLUMNS WHERE table_name = {{cat1}}" in bare


def test_oracle_metadata_reads_all_tab_columns_with_the_current_schema() -> None:
    from factlang.ir import MetadataQuery

    probe = render(MetadataQuery(ORDERS), prof("oracle"))
    assert "FROM all_tab_columns" in probe.sql_template
    assert "table_schema = SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')" in probe.sql_template
    assert [p.value for p in probe.params] == ["ORDERS"]  # unquoted names fold to upper


@pytest.mark.parametrize("name", NEW)
def test_unsupported_methods_are_fl506_and_never_limit(name: str) -> None:
    p = prof(name)
    for method in ("system", "bernoulli"):
        plan = ColumnAggregate(ORDERS, ID, (Metric.ROW_COUNT,), sample=SampleSpec(
            method, Decimal("0.1"), seed=3))  # type: ignore[arg-type]  # fmt: skip
        if p.sample_styles.get(method) is None:
            with pytest.raises(RenderError, match="FL506"):
                render(plan, p)
        else:
            text = render(plan, p).sql_template
            assert "LIMIT" not in text and "TOP" not in text and "FETCH" not in text
            assert "(10" in text and "3)" in text  # percent and seed are written


def test_seed_ranges() -> None:
    assert prof("snowflake").sample_seed_range == (0, 2**31 - 1)
    assert prof("oracle").sample_seed_range == (0, 4294967295)
    plan = ColumnAggregate(ORDERS, ID, (Metric.ROW_COUNT,),
                           sample=SampleSpec("system", Decimal("0.1"), seed=2**31))  # fmt: skip
    with pytest.raises(RenderError, match="FL506"):
        render(plan, prof("snowflake"))


def test_no_factlang_alias_is_a_reserved_word_anywhere() -> None:
    """An alias that is reserved would be quoted as written but referenced folded (e.g.
    ``"value"`` vs ``"VALUE"`` in Snowflake); FactLang's own names must never be reserved."""
    from factlang.ir import ALIASES

    names = set(ALIASES.values()) | {
        "kind", "value", "freq", "key_rows", "k1", "k2", "k3", "distinct_left", "matched",
        "left_non_null", "matched_left_rows", "distinct_right", "max_left_per_key",
        "max_right_per_key", "sampled_rows", "sampled_left_rows", "sampled_right_rows",
        "column_name", "data_type", "declared_rows", "undeclared_distinct", "non_null",
        "matching", "g", "a", "b", "c", "d", "l", "r", "u", "v", "t1", "t2", "t3", "fl_d1",
        "fl_cols", "table_name", "table_schema", "table_catalog",
    }  # fmt: skip
    for name in native_dialects():
        assert not {n for n in names if prof(name).is_reserved(n)}, name
