"""Databricks (Spark SQL) profile (tier ``native``: parsed with sqlglot in CI, not executed).

Slots: catalog.schema.table (Unity Catalog). Backtick quoting. ``TABLESAMPLE (p PERCENT)
REPEATABLE (seed)`` samples rows independently (Bernoulli), so it is the ``bernoulli``
method; there is no block sample (``system`` is FL506).
"""

from __future__ import annotations

from factlang.sql import ast as A
from factlang.sql.profile import DialectProfile
from factlang.sql.profiles._shared import derived_union_subquery

# Spark SQL keywords reserved under ANSI mode (quoting a non-reserved word is harmless:
# Databricks identifiers are case-insensitive, quoted or not).
_RESERVED_WORDS = """
    all alter and anti any array as at authorization between both by case cast check
    collate column commit constraint create cross cube current current_date current_time
    current_timestamp current_user delete describe distinct drop else end escape except
    exists external extract false fetch filter for foreign from full function global grant
    group grouping having in inner insert intersect interval into is join lateral leading
    left like local minus natural no not null of on only or order out outer overlaps
    partition position primary range references revoke right rollback rollup row rows
    select semi session_user set some start table tablesample then time to trailing true
    truncate union unique unknown update user using values when where window with
"""
RESERVED = frozenset(_RESERVED_WORDS.split())

TYPE_FAMILIES: tuple[tuple[str, str], ...] = (
    (r"array\s*<.*>", "array"),
    (r"struct\s*<.*>", "struct"),
    (r"tinyint|smallint|int|integer|bigint|byte|short|long", "integer"),
    (r"(decimal|dec|numeric)(\s*\(.*\))?", "decimal"),
    (r"float|double|real", "float"),
    (r"string|varchar\s*\(\d+\)|char\s*\(\d+\)", "string"),
    (r"boolean", "boolean"),
    (r"date", "date"),
    (r"timestamp(_ntz|_ltz)?", "timestamp"),
    (r"binary", "bytes"),
    (r"variant", "json"),
)

_PARAM_TYPES = {
    "integer": "BIGINT",
    "decimal": "DECIMAL(38,9)",
    "float": "DOUBLE",
    "string": "STRING",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
}

DATABRICKS = DialectProfile(
    name="databricks",
    tier="native",
    quote_char="`",
    unquoted_case="preserve",
    reserved_words=RESERVED,
    max_table_parts=3,
    max_identifier_length=255,
    count_if_style="count_if",
    limit_style="limit",
    cast_types={**_PARAM_TYPES, "bytes": "BINARY"},  # no TIME type; JSON is a STRING
    now_sql="current_timestamp()",
    type_families=TYPE_FAMILIES,
    param_types=_PARAM_TYPES,
    # full_data_type keeps precision and scale (data_type is just DECIMAL)
    information_schema_sql=(
        "SELECT column_name, full_data_type AS data_type FROM information_schema.columns "
        "WHERE {where}"
    ),
    qualified_information_schema_sql=(
        "SELECT column_name, full_data_type AS data_type FROM {prefix}information_schema.columns "
        "WHERE {where}"
    ),
    # Unity Catalog stores names in lower case and resolves them case-insensitively, quoted or not
    quoted_catalog_match="lower",
    unquoted_catalog_match="lower",
    # column names keep the case they were created with and resolve case-insensitively
    quoted_column_match="casefold",
    unquoted_column_match="casefold",
    overrides={A.SubqueryFrom: derived_union_subquery},
    sample_styles={
        "system": None,
        "bernoulli": "TABLESAMPLE ({percent} PERCENT) REPEATABLE ({seed})",
    },
    sample_seed_range=(0, 2**31 - 1),  # the REPEATABLE seed is read as an INT literal
    # spec 19 keyhash ruling: xxhash64 is a signed BIGINT, folded with MOD(MOD(h,B)+B,B)
    keyhash_expr="MOD(MOD(xxhash64({s}), 1000000) + 1000000, 1000000)",
    default_schema_sql="current_schema()",
    default_catalog_sql="current_catalog()",
    # a string literal escapes ' with a backslash only; '' is two adjacent literals
    doubled_quote_escape=False,
)

__all__ = ["DATABRICKS"]
