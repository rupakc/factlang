"""Snowflake profile (tier ``native``: SQL parsed with sqlglot in CI, not executed).

Slots: database.schema.table. Unquoted names fold to upper case. Metadata for a ref that
names a database reads ``<database>.information_schema.columns``. Only ``SAMPLE BERNOULLI
(p) SEED (n)`` is declared (seed 0 … 2^31-1); a block (``system``) sample's repeatability is
not proven in CI, so it is FL506 (ruling, Task 15 review).
"""

from __future__ import annotations

from factlang.sql.profile import DialectProfile

# Snowflake reserved and limited keywords ("cannot be used as an identifier").
_RESERVED_WORDS = """
    account all alter and any as between by case cast check column connect connection
    constraint create cross current current_date current_time current_timestamp
    current_user database delete distinct drop else exists false following for from full
    grant group gscluster having ilike in increment inner insert intersect into is issue
    join lateral left like localtime localtimestamp minus natural not null of on or order
    organization qualify regexp revoke right rlike row rows sample schema select set some
    start table tablesample then to trigger true try_cast union unique update using values
    view when whenever where with
"""
RESERVED = frozenset(_RESERVED_WORDS.split())

TYPE_FAMILIES: tuple[tuple[str, str], ...] = (
    (r"array", "array"),
    (r"number|decimal|numeric|number\s*\(.*\)|decimal\s*\(.*\)|numeric\s*\(.*\)", "decimal"),
    (r"int|integer|bigint|smallint|tinyint|byteint", "integer"),
    (r"float[48]?|double( precision)?|real", "float"),
    (r"varchar(\s*\(\d+\))?|text|string|char(acter)?(\s*\(\d+\))?", "string"),
    (r"boolean", "boolean"),
    (r"date", "date"),
    (r"timestamp(_ntz|_ltz|_tz)?(\s*\(\d+\))?|datetime", "timestamp"),
    (r"time(\s*\(\d+\))?", "time"),
    (r"(var)?binary(\s*\(\d+\))?", "bytes"),
    (r"variant|object", "json"),
)

_COLUMNS = (
    "SELECT column_name, CASE WHEN data_type = 'NUMBER' AND numeric_scale = 0 "
    "THEN 'NUMBER(' || numeric_precision || ',0)' ELSE data_type END AS data_type "
    "FROM {source} WHERE {where}"
)

SNOWFLAKE = DialectProfile(
    name="snowflake",
    doubled_quote_escape=True,  # the lexer reads '' as one ' (conformance-checked)
    tier="native",
    quote_char='"',
    unquoted_case="upper",
    reserved_words=RESERVED,
    max_table_parts=3,
    max_identifier_length=255,
    count_if_style="count_if",
    limit_style="limit",
    cast_types={
        "integer": "NUMBER(38,0)",
        "decimal": "NUMBER(38,9)",
        "float": "FLOAT",
        "string": "VARCHAR",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "timestamp": "TIMESTAMP_TZ",
        "time": "TIME",
        "bytes": "BINARY",
        "json": "VARIANT",
    },
    now_sql="CURRENT_TIMESTAMP()",
    type_families=TYPE_FAMILIES,
    param_types={
        "integer": "NUMBER(38,0)",
        "decimal": "NUMBER(38,9)",
        "float": "FLOAT",
        "string": "VARCHAR",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "timestamp": "TIMESTAMP_TZ",
    },
    # data_type is NUMBER for every fixed-point column; scale 0 is spelled out so that
    # INTEGER columns map to the integer family (spec 6.1).
    information_schema_sql=_COLUMNS.format(source="information_schema.columns", where="{where}"),
    qualified_information_schema_sql=_COLUMNS.format(
        source="{prefix}information_schema.columns", where="{where}"
    ),
    quoted_catalog_match="exact",
    unquoted_catalog_match="upper",
    sample_styles={
        "system": None,
        "bernoulli": "SAMPLE BERNOULLI ({percent}) SEED ({seed})",
    },
    sample_seed_range=(0, 2**31 - 1),
    # spec 19 keyhash ruling: HASH is a signed 64-bit value, folded with MOD(MOD(h,B)+B,B)
    keyhash_expr="MOD(MOD(HASH({s}), 1000000) + 1000000, 1000000)",
    default_schema_sql="CURRENT_SCHEMA()",
    default_catalog_sql="CURRENT_DATABASE()",
)

__all__ = ["SNOWFLAKE"]
