"""PostgreSQL profile (tier ``verified``: executed against a service container in CI)."""

from __future__ import annotations

from factlang.sql.profile import DialectProfile

# PostgreSQL key words marked "reserved" and "reserved (can be function or type)": neither
# may name a table or column unquoted.
_RESERVED_WORDS = """
    all analyse analyze and any array as asc asymmetric both case cast check collate column
    constraint create current_catalog current_date current_role current_time
    current_timestamp current_user default deferrable desc distinct do else end except false
    fetch for foreign from grant group having in initially intersect into lateral leading
    limit localtime localtimestamp not null offset on only or order placing primary
    references returning select session_user some symmetric system_user table then to
    trailing true union unique user using variadic when where window with
    authorization binary collation concurrently cross current_schema freeze full ilike inner
    is isnull join left like natural notnull outer overlaps right similar tablesample verbose
"""
RESERVED = frozenset(_RESERVED_WORDS.split())

TYPE_FAMILIES: tuple[tuple[str, str], ...] = (
    (r"array|.*\[\d*\]|_.+", "array"),
    (r"smallint|integer|bigint|int[248]?|smallserial|serial[248]?|bigserial|oid", "integer"),
    (r"(numeric|decimal)(\s*\(.*\))?", "decimal"),
    (r"real|double precision|float[48]?", "float"),
    (
        r"character varying(\s*\(.*\))?|varchar(\s*\(.*\))?|character(\s*\(.*\))?"
        r"|char(\s*\(.*\))?|bpchar|text|name|citext",
        "string",
    ),
    (r"bool(ean)?", "boolean"),
    (r"date", "date"),
    (r"timestamp(\s*\(\d+\))?( with(out)? time zone)?|timestamptz", "timestamp"),
    (r"time(\s*\(\d+\))?( with(out)? time zone)?|timetz", "time"),
    (r"bytea", "bytes"),
    (r"jsonb?", "json"),
)

POSTGRES = DialectProfile(
    name="postgres",
    tier="verified",
    quote_char='"',
    unquoted_case="lower",
    reserved_words=RESERVED,
    max_table_parts=3,
    max_identifier_length=63,  # NAMEDATALEN - 1 bytes; longer names are silently truncated
    count_if_style="filter",
    limit_style="limit",
    cast_types={
        "integer": "BIGINT",
        "decimal": "NUMERIC",
        "float": "DOUBLE PRECISION",
        "string": "TEXT",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
        "time": "TIME",
        "bytes": "BYTEA",
        "json": "JSONB",
    },
    now_sql="CURRENT_TIMESTAMP",
    type_families=TYPE_FAMILIES,
    param_types={
        "integer": "BIGINT",
        "decimal": "NUMERIC",
        "float": "DOUBLE PRECISION",
        "string": "TEXT",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
    },
    information_schema_sql=(
        "SELECT column_name, data_type FROM information_schema.columns WHERE {where}"
    ),
    quoted_catalog_match="exact",
    unquoted_catalog_match="lower",
    sample_styles={
        "system": "TABLESAMPLE SYSTEM ({percent}) REPEATABLE ({seed})",
        "bernoulli": "TABLESAMPLE BERNOULLI ({percent}) REPEATABLE ({seed})",
    },
    # spec 19 keyhash ruling: signed BIGINT, folded with MOD(MOD(h, B) + B, B) (PG >= 11)
    keyhash_expr="MOD(MOD(hashtextextended({s}, 0), 1000000) + 1000000, 1000000)",
    cast_params=True,  # every bind parameter is typed in the SQL text
    default_schema_sql="ANY (current_schemas(false))",  # every schema on the search_path
)

__all__ = ["POSTGRES"]
