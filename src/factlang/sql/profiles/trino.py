"""Trino profile (tier ``verified``, Amendment A R7: executed against a ``trinodb/trino``
service container in CI, Task 20).

Slots: catalog.schema.table. Unquoted names fold to lower case. ``TABLESAMPLE`` takes no
seed, so it cannot repeat a sample and neither table-sample method is declared (FL506);
keyhash is supported.
"""

from __future__ import annotations

from factlang.sql.profile import DialectProfile

# Trino reserved keywords (SQL language reference, "Reserved keywords").
_RESERVED_WORDS = """
    alter and as between by case cast constraint create cross cube current_catalog
    current_date current_path current_role current_schema current_time current_timestamp
    current_user deallocate delete describe distinct drop else end escape except execute
    exists extract false for from full group grouping having in inner insert intersect into
    is join json_array json_exists json_object json_query json_table json_value left like
    listagg localtime localtimestamp natural normalize not null on or order outer prepare
    recursive right rollup select skip table then trim true uescape union unnest using
    values when where with
"""
RESERVED = frozenset(_RESERVED_WORDS.split())

TYPE_FAMILIES: tuple[tuple[str, str], ...] = (
    (r"array\s*\(.*\)", "array"),
    (r"row\s*\(.*\)", "struct"),
    (r"tinyint|smallint|integer|int|bigint", "integer"),
    (r"decimal(\s*\(.*\))?", "decimal"),
    (r"real|double", "float"),
    (r"varchar(\s*\(\d+\))?|char(\s*\(\d+\))?", "string"),
    (r"boolean", "boolean"),
    (r"date", "date"),
    (r"timestamp(\s*\(\d+\))?( with time zone)?", "timestamp"),
    (r"time(\s*\(\d+\))?( with time zone)?", "time"),
    (r"varbinary", "bytes"),
    (r"json", "json"),
)

_PARAM_TYPES = {
    "integer": "bigint",
    "decimal": "decimal",
    "float": "double",
    "string": "varchar",
    "boolean": "boolean",
    "date": "date",
    "timestamp": "timestamp(6) with time zone",
}

TRINO = DialectProfile(
    name="trino",
    tier="verified",
    quote_char='"',
    unquoted_case="lower",
    reserved_words=RESERVED,
    max_table_parts=3,
    max_identifier_length=1024,
    count_if_style="count_if",
    limit_style="limit",
    cast_types={
        **_PARAM_TYPES,
        "decimal": "decimal(38,9)",
        "time": "time(6)",
        "bytes": "varbinary",
        "json": "json",
    },
    now_sql="current_timestamp",
    type_families=TYPE_FAMILIES,
    param_types=_PARAM_TYPES,
    information_schema_sql=(
        "SELECT column_name, data_type FROM information_schema.columns WHERE {where}"
    ),
    qualified_information_schema_sql=(
        "SELECT column_name, data_type FROM {prefix}information_schema.columns WHERE {where}"
    ),
    # Trino lower-cases every identifier, quoted ones included, before asking a connector
    quoted_catalog_match="lower",
    unquoted_catalog_match="lower",
    sample_styles={"system": None, "bernoulli": None},
    # spec 19 keyhash ruling: from_big_endian_64(xxhash64(…)) is a signed BIGINT, folded
    # with MOD(MOD(h,B)+B,B)
    keyhash_expr=(
        "MOD(MOD(from_big_endian_64(xxhash64(to_utf8({s}))), 1000000) + 1000000, 1000000)"
    ),
    default_schema_sql="current_schema",
    default_catalog_sql="current_catalog",
)

__all__ = ["TRINO"]
