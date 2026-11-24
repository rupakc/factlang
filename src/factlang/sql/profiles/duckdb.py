"""DuckDB profile (tier ``verified``: executed in CI, in-memory)."""

from __future__ import annotations

from factlang.sql.profile import DialectProfile

# duckdb_keywords() categories 'reserved' and 'type_function': neither may name a column
# or table unquoted.
_RESERVED_WORDS = """
    all analyse analyze and anti any array as asc asof asymmetric at authorization binary
    both by case cast check collate collation column columns concurrently constraint create
    cross default deferrable desc describe distinct do else end except false fetch for
    foreign freeze from full generated glob group having ilike in initially inner intersect
    into is isnull join lambda lateral leading left like limit map natural not notnull null
    offset on only or order outer overlaps pivot pivot_longer pivot_wider placing positional
    primary qualify references returning right select semi show similar some struct summarize
    symmetric table tablesample then to trailing true try_cast tuple union unique unpack
    unpivot using variadic verbose when where window with
"""
RESERVED = frozenset(_RESERVED_WORDS.split())

TYPE_FAMILIES: tuple[tuple[str, str], ...] = (
    (r".*\[\d*\]|list\s*\(.*\)", "array"),
    (r"struct\s*\(.*\)|row\s*\(.*\)", "struct"),
    (
        r"u?(tinyint|smallint|integer|int|bigint|hugeint)|int[1248]|int128|long|short|signed",
        "integer",
    ),
    (r"(decimal|numeric)(\s*\(.*\))?", "decimal"),
    (r"double|float[48]?|float|real", "float"),
    (r"varchar(\s*\(.*\))?|text|string|char(\s*\(.*\))?|bpchar", "string"),
    (r"bool(ean)?|logical", "boolean"),
    (r"date", "date"),
    (r"timestamp.*|datetime", "timestamp"),
    (r"time(tz)?|time with(out)? time zone", "time"),
    (r"blob|bytea|binary|varbinary", "bytes"),
    (r"json", "json"),
)

DUCKDB = DialectProfile(
    name="duckdb",
    tier="verified",
    quote_char='"',
    unquoted_case="preserve",
    reserved_words=RESERVED,
    max_table_parts=3,
    max_identifier_length=1024,
    count_if_style="count_if",
    limit_style="limit",
    cast_types={
        "integer": "BIGINT",
        "decimal": "DECIMAL(38,9)",
        "float": "DOUBLE",
        "string": "VARCHAR",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
        "time": "TIME",
        "bytes": "BLOB",
        "json": "JSON",
    },
    now_sql="CURRENT_TIMESTAMP",
    string_type="VARCHAR",
    type_families=TYPE_FAMILIES,
    param_types={
        "integer": "BIGINT",
        "decimal": "DECIMAL(38,9)",
        "float": "DOUBLE",
        "string": "VARCHAR",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
    },
    information_schema_sql=(
        "SELECT column_name, data_type FROM information_schema.columns WHERE {where}"
    ),
    sample_styles={
        "system": "TABLESAMPLE {percent}% (system, {seed})",
        "bernoulli": "TABLESAMPLE {percent}% (bernoulli, {seed})",
    },
    # spec 19 keyhash ruling: hash() is UBIGINT, so no sign fold is needed
    keyhash_expr="(hash({s}) % 1000000)",
    sample_seed_range=(0, 2**63 - 1),
    default_schema_sql="current_schema()",
    default_catalog_sql="current_database()",
)

__all__ = ["DUCKDB"]
