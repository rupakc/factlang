"""T-SQL profile: SQL Server, Azure Synapse, Fabric (tier ``verified``: executed against a
service container in CI, Task 20).

Slots: database.schema.table. Identifiers are quoted with ``"`` (QUOTED_IDENTIFIER ON, the
ODBC/JDBC driver default), never with brackets. Spellings that differ from the ANSI printer:
``TOP n``; ``COUNT_BIG`` (``COUNT`` is a 32-bit INT and overflows past 2^31 rows); a
conditional count is ``COUNT_BIG(CASE WHEN c THEN 1 END)``; ``AVG`` is taken over ``x + 0.0``
(an integer AVG truncates); string concatenation is ``+``; the part before a separator is
``LEFT(x, CHARINDEX(sep, x + sep) - 1)``; ``SUM`` is taken over ``x + CONVERT(BIGINT, 0)``
(an INT sum would overflow; the additive form is exact for every DECIMAL(p,s) with s <= 18
and p <= 37, see :func:`~factlang.sql.profiles._shared.widened_aggregates` for the rule and
the residual). Metadata for a ref that names a database reads
``<db>.INFORMATION_SCHEMA.COLUMNS``, with DECIMAL/NUMERIC spelled ``decimal(p,s)`` so scale-0
columns map to the integer family. No table-sample method is declared (FL506): page-level
``TABLESAMPLE … REPEATABLE`` repeatability is not proven in CI (ruling, Task 15 review), and
there is no row sample; keyhash is supported.
"""

from __future__ import annotations

from factlang.sql import ast as A
from factlang.sql.printer import Printer
from factlang.sql.profile import DialectProfile
from factlang.sql.profiles._shared import widened_aggregates

# T-SQL reserved keywords (Transact-SQL reference, "Reserved keywords").
_RESERVED_WORDS = """
    add all alter and any as asc authorization backup begin between break browse bulk by
    cascade case check checkpoint close clustered coalesce collate column commit compute
    constraint contains containstable continue convert create cross current current_date
    current_time current_timestamp current_user cursor database dbcc deallocate declare
    default delete deny desc disk distinct distributed double drop dump else end errlvl
    escape except exec execute exists exit external fetch file fillfactor for foreign
    freetext freetexttable from full function goto grant group having holdlock identity
    identity_insert identitycol if in index inner insert intersect into is join key kill
    left like lineno load merge national nocheck nonclustered not null nullif of off
    offsets on open opendatasource openquery openrowset openxml option or order outer over
    percent pivot plan precision primary print proc procedure public raiserror read
    readtext reconfigure references replication restore restrict return revert revoke
    right rollback rowcount rowguidcol rule save schema securityaudit select
    semantickeyphrasetable semanticsimilaritydetailstable semanticsimilaritytable
    session_user set setuser shutdown some statistics system_user table tablesample
    textsize then to top tran transaction trigger truncate try_convert tsequal union unique
    unpivot update updatetext use user values varying view waitfor when where while with
    within writetext
"""
RESERVED = frozenset(_RESERVED_WORDS.split())

TYPE_FAMILIES: tuple[tuple[str, str], ...] = (
    (r"tinyint|smallint|int|bigint", "integer"),
    (r"(decimal|numeric)(\s*\(.*\))?|(small)?money", "decimal"),
    (r"float(\s*\(\d+\))?|real", "float"),
    (r"n?varchar(\s*\((\d+|max)\))?|n?char(\s*\(\d+\))?|n?text|sysname", "string"),
    (r"bit", "boolean"),
    (r"date", "date"),
    (r"datetime|datetime2(\s*\(\d+\))?|smalldatetime|datetimeoffset(\s*\(\d+\))?", "timestamp"),
    (r"time(\s*\(\d+\))?", "time"),
    (r"(var)?binary(\s*\((\d+|max)\))?|image|rowversion|timestamp", "bytes"),
    (r"json", "json"),
)


def _count_if(printer: Printer, x: A.CountIf) -> str:
    return f"COUNT_BIG(CASE WHEN {printer.expr(x.cond)} THEN 1 END)"


def _split_prefix(printer: Printer, x: A.SplitPrefix) -> str:
    e, sep = printer.expr(x.e), printer.expr(x.separator)
    return f"LEFT({e}, CHARINDEX({sep}, {e} + {sep}) - 1)"


_PARAM_TYPES = {
    "integer": "BIGINT",
    "decimal": "DECIMAL(38,9)",
    "float": "FLOAT",
    "string": "NVARCHAR(4000)",
    "boolean": "BIT",
    "date": "DATE",
    "timestamp": "DATETIME2",
}

_COLUMNS = (
    "SELECT column_name, CASE WHEN data_type IN ('decimal', 'numeric') THEN data_type + '(' + "
    "CAST(numeric_precision AS VARCHAR(10)) + ',' + CAST(numeric_scale AS VARCHAR(10)) + ')' "
    "ELSE data_type END AS data_type FROM {source} WHERE {where}"
)

TSQL = DialectProfile(
    name="tsql",
    tier="verified",
    quote_char='"',
    unquoted_case="preserve",
    reserved_words=RESERVED,
    max_table_parts=3,
    max_identifier_length=128,
    count_if_style="sum_case",  # spelled COUNT_BIG(CASE …) by the CountIf override
    limit_style="top",
    cast_types={**_PARAM_TYPES, "time": "TIME", "bytes": "VARBINARY(MAX)"},
    now_sql="SYSUTCDATETIME()",
    type_families=TYPE_FAMILIES,
    param_types=_PARAM_TYPES,
    information_schema_sql=_COLUMNS.format(source="INFORMATION_SCHEMA.COLUMNS", where="{where}"),
    qualified_information_schema_sql=_COLUMNS.format(
        source="{prefix}INFORMATION_SCHEMA.COLUMNS", where="{where}"
    ),
    # names compare under the database collation, case-insensitive by default
    quoted_catalog_match="casefold",
    unquoted_catalog_match="casefold",
    overrides={
        A.Agg: widened_aggregates("COUNT_BIG", sum_widen="CONVERT(BIGINT, 0)"),
        A.CountIf: _count_if,
        A.SplitPrefix: _split_prefix,
    },
    sample_styles={"system": None, "bernoulli": None},
    # spec 19 keyhash ruling: 64 signed bits of MD5, folded with MOD(MOD(h,B)+B,B) (``%``)
    keyhash_expr=(
        "((CONVERT(BIGINT, SUBSTRING(HASHBYTES('MD5', CAST({s} AS NVARCHAR(4000))), 1, 8))"
        " % 1000000 + 1000000) % 1000000)"
    ),
    string_concat="+",
    default_schema_sql="SCHEMA_NAME()",
    default_catalog_sql="DB_NAME()",
)

__all__ = ["TSQL"]
