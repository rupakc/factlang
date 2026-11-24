"""BigQuery (GoogleSQL) profile (tier ``native``: SQL parsed with sqlglot in CI, not executed).

Slots: project.dataset.table. Backtick quoting; inside backticks a backslash is an escape,
so quoted names are written with ``\\``/``\\``` escapes (:func:`backslash_quoting`).
``TABLESAMPLE SYSTEM`` takes no seed, so it cannot repeat a sample and is not declared
(FL506); keyhash is the only sample method.
"""

from __future__ import annotations

from factlang.semantics.refs import Ident, TableRef
from factlang.sql import ast as A
from factlang.sql.printer import Printer
from factlang.sql.profile import DialectProfile
from factlang.sql.profiles._shared import backslash_quoting

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

_column, _table_name = backslash_quoting("\\`")


def _split_prefix(printer: Printer, x: A.SplitPrefix) -> str:
    # SPLIT never returns an empty array for a non-NULL input, so OFFSET(0) always exists.
    return f"SPLIT({printer.expr(x.e)}, {printer.expr(x.separator)})[SAFE_OFFSET(0)]"


def _catalog_columns(printer: Printer, x: A.CatalogColumns) -> str:
    """``INFORMATION_SCHEMA.COLUMNS`` of the table's dataset (the job's default dataset when
    the ref names none); BigQuery table names are case-sensitive, so names match exactly."""
    ref = x.table
    if len(ref.parts()) > printer.profile.max_table_parts:
        printer.table(ref)  # raises FL505
    if ref.schema is not None:
        view = TableRef(ref.catalog, ref.schema, Ident("INFORMATION_SCHEMA"))
        source = _table_name(printer, A.TableName(view)) + ".COLUMNS"
    else:
        source = "INFORMATION_SCHEMA.COLUMNS"
    printer.check_ident(ref.table.text, ref.table.quoted)
    conds = [f"table_name = {printer._catalog_param(ref.table.text)}"]
    if x.column is not None:
        printer.check_ident(x.column.text, x.column.quoted)
        conds.append(f"column_name = {printer._catalog_param(x.column.text)}")
    sql = f"SELECT column_name, data_type FROM {source} WHERE {' AND '.join(conds)}"
    return f"({sql}) {printer._derived_alias()}"


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
    quoted_catalog_match="exact",
    unquoted_catalog_match="exact",
    overrides={
        A.Column: _column,
        A.TableName: _table_name,
        A.SplitPrefix: _split_prefix,
        A.CatalogColumns: _catalog_columns,
    },
    sample_styles={"system": None, "bernoulli": None},
    # spec 19 keyhash ruling: FARM_FINGERPRINT is a signed INT64, folded with MOD(MOD(h,B)+B,B)
    keyhash_expr="MOD(MOD(FARM_FINGERPRINT({s}), 1000000) + 1000000, 1000000)",
)

__all__ = ["BIGQUERY"]
