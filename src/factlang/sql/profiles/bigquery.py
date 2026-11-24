"""BigQuery (GoogleSQL) profile (tier ``native``: SQL parsed with sqlglot in CI, not executed).

Slots: project.dataset.table. Backtick quoting; inside backticks a backslash is an escape,
so in a quoted name a backslash and a backtick are each written after a backslash
(``quote_escapes``). Column metadata comes from the
dataset's ``INFORMATION_SCHEMA.COLUMNS`` (the job's default dataset when the ref names none);
table names match exactly (they are case-sensitive), column names case-insensitively.
``TABLESAMPLE SYSTEM`` takes no seed, so it cannot repeat a sample and is not declared
(FL506); keyhash is the only sample method.
"""

from __future__ import annotations

from factlang.sql import ast as A
from factlang.sql.printer import Printer
from factlang.sql.profile import DialectProfile

# GoogleSQL reserved keywords (lexical structure reference).
_RESERVED_WORDS = """
    all and any array as asc assert_rows_modified at between by case cast collate contains
    create cross cube current default define desc distinct else end enum escape except
    exclude exists extract false fetch following for from full group grouping groups hash
    having if ignore in inner intersect interval into is join lateral left like limit
    lookup merge natural new no not null nulls of on or order outer over partition
    preceding proto qualify range recursive respect right rollup rows select set some
    struct tablesample then to treat true unbounded union unnest using when where window
    with within
"""
RESERVED = frozenset(_RESERVED_WORDS.split())

TYPE_FAMILIES: tuple[tuple[str, str], ...] = (
    (r"array\s*<.*>", "array"),
    (r"struct\s*<.*>", "struct"),
    (r"int64|int|smallint|integer|bigint|tinyint|byteint", "integer"),
    (r"bignumeric\s*\(\s*\d+\s*(,\s*0+\s*)?\)|bigdecimal\s*\(\s*\d+\s*(,\s*0+\s*)?\)", "integer"),
    (r"(big)?(numeric|decimal)(\s*\(.*\))?", "decimal"),
    (r"float64", "float"),
    (r"string(\s*\(\d+\))?", "string"),
    (r"bool(ean)?", "boolean"),
    (r"date", "date"),
    (r"timestamp|datetime", "timestamp"),
    (r"time", "time"),
    (r"bytes(\s*\(\d+\))?", "bytes"),
    (r"json", "json"),
)


def _split_prefix(printer: Printer, x: A.SplitPrefix) -> str:
    # SPLIT never returns an empty array for a non-NULL input, so OFFSET(0) always exists.
    return f"SPLIT({printer.expr(x.e)}, {printer.expr(x.separator)})[SAFE_OFFSET(0)]"


BIGQUERY = DialectProfile(
    name="bigquery",
    tier="native",
    quote_char="`",
    unquoted_case="preserve",
    reserved_words=RESERVED,
    max_table_parts=3,
    max_identifier_length=300,  # column names; table and dataset names allow more
    count_if_style="countif",
    limit_style="limit",
    cast_types={
        "integer": "INT64",
        "decimal": "NUMERIC",
        "float": "FLOAT64",
        "string": "STRING",
        "boolean": "BOOL",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
        "time": "TIME",
        "bytes": "BYTES",
        "json": "JSON",
    },
    now_sql="CURRENT_TIMESTAMP()",
    type_families=TYPE_FAMILIES,
    param_types={
        "integer": "INT64",
        "decimal": "NUMERIC",
        "float": "FLOAT64",
        "string": "STRING",
        "boolean": "BOOL",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
    },
    information_schema_sql=(
        "SELECT column_name, data_type FROM INFORMATION_SCHEMA.COLUMNS WHERE {where}"
    ),
    qualified_information_schema_sql=(
        "SELECT column_name, data_type FROM {prefix}INFORMATION_SCHEMA.COLUMNS WHERE {where}"
    ),
    information_schema_scope="schema",
    quoted_catalog_match="exact",
    unquoted_catalog_match="exact",
    quoted_column_match="casefold",  # column names are case-insensitive, quoted or not
    unquoted_column_match="casefold",
    quote_escapes={"`": "\\`", "\\": "\\\\"},
    overrides={A.SplitPrefix: _split_prefix},
    sample_styles={"system": None, "bernoulli": None},
    # spec 19 keyhash ruling: FARM_FINGERPRINT is a signed INT64, folded with MOD(MOD(h,B)+B,B)
    keyhash_expr="MOD(MOD(FARM_FINGERPRINT({s}), 1000000) + 1000000, 1000000)",
    # a string literal escapes ' with a backslash only; '' is two adjacent literals
    doubled_quote_escape=False,
)

__all__ = ["BIGQUERY"]
