"""ClickHouse profile (tier ``verified``: executed against a service container in CI, Task 20).

Slots: database.table (2). Identifiers are case-sensitive. Spellings that differ from the
ANSI printer:

- ``countIf(c)`` (never NULL);
- the part before a separator is ``splitByString(sep, assumeNotNull(x))[1]`` (NULL stays
  NULL);
- a typed NULL is a bare ``NULL`` (``Nullable(Nothing)`` unifies with ``UInt64`` counts in a
  UNION ALL; ``Nullable(Int64)`` does not), every other cast targets a ``Nullable`` type;
- a LEFT JOIN fills unmatched right columns with type defaults unless
  ``join_use_nulls = 1``, so a subquery holding one carries that setting;
- inside quotes a backslash is an escape, so quoted names have it doubled
  (``quote_escapes``);
- a bare name resolves to a SELECT alias before a column (the membership summary's
  ``NULL AS value`` would capture a user column named ``value``), so every statement ends
  ``SETTINGS prefer_column_name_to_alias = 1``;
- ``max``/``min``/``sum``/``avg`` of no rows give the type's default (0, or nan) unless the
  input is Nullable, so they are spelled ``maxOrNull`` etc.: no rows stays NULL (``NO_DATA``),
  never a measured 0.

``SAMPLE`` needs a table-level ``SAMPLE BY`` key, so neither table-sample method is
declared (FL506); keyhash is supported.
"""

from __future__ import annotations

from factlang.sql import ast as A
from factlang.sql.printer import Printer
from factlang.sql.profile import DialectProfile
from factlang.sql.profiles._shared import derived_union_subquery

# ClickHouse reserves almost nothing; these keywords are quoted because the parser reads
# them as clause starters where a bare column name could appear.
_RESERVED_WORDS = """
    all and any array as asc between by case cast cross desc distinct else end except false
    final format from full global group having ilike in inner intersect interval into is
    join left like limit not null offset on or order outer prewhere right sample select
    settings then union using when where window with
"""
RESERVED = frozenset(_RESERVED_WORDS.split())

TYPE_FAMILIES: tuple[tuple[str, str], ...] = (
    (r"array\s*\(.*\)", "array"),
    (r"tuple\s*\(.*\)|nested\s*\(.*\)", "struct"),
    (r"u?int(8|16|32|64|128|256)", "integer"),
    (r"decimal(32|64|128|256)\s*\(\s*0+\s*\)", "integer"),
    (r"decimal(32|64|128|256)?\s*\(.*\)", "decimal"),
    (r"float(32|64)|bfloat16", "float"),
    (r"string|fixedstring\s*\(\d+\)|enum(8|16)?\s*\(.*\)", "string"),
    (r"bool(ean)?", "boolean"),
    (r"date(32)?", "date"),
    (r"datetime(64)?(\s*\(.*\))?", "timestamp"),
    (r"time(64)?(\s*\(.*\))?", "time"),
    (r"json|object\s*\(\s*'json'\s*\)", "json"),
)


def _count_if(printer: Printer, x: A.CountIf) -> str:
    return f"countIf({printer.expr(x.cond)})"


_OR_NULL = {"MIN": "minOrNull", "MAX": "maxOrNull", "SUM": "sumOrNull", "AVG": "avgOrNull"}


def _agg(printer: Printer, x: A.Agg) -> str:
    arg = "*" if x.arg is None else printer.expr(x.arg)
    return f"{_OR_NULL.get(x.fn, x.fn)}({'DISTINCT ' if x.distinct else ''}{arg})"


def _split_prefix(printer: Printer, x: A.SplitPrefix) -> str:
    # splitByString takes no Nullable string: split the non-NULL value, keep NULL as NULL
    # (the summary branch reads NULL rows too, and must not see them as '')
    e, sep = printer.expr(x.e), printer.expr(x.separator)
    return f"if({e} IS NULL, NULL, splitByString({sep}, assumeNotNull({e}))[1])"


def _cast(printer: Printer, x: A.Cast) -> str:
    if isinstance(x.e, A.Literal) and x.e.value is None:
        return "NULL"
    return f"CAST({printer.expr(x.e)} AS {printer.cast_type(x.family)})"


def _has_left_join(item: object) -> bool:
    if isinstance(item, A.Join):
        return item.kind == "LEFT" or _has_left_join(item.left) or _has_left_join(item.right)
    return False


def _subquery(printer: Printer, x: A.SubqueryFrom) -> str:
    if isinstance(x.select, A.Select) and _has_left_join(x.select.from_):
        inner = printer.select(x.select)
        return f"({inner} SETTINGS join_use_nulls = 1) {printer.alias(x.alias)}"
    return derived_union_subquery(printer, x)


_PARAM_TYPES = {
    "integer": "Int64",
    "decimal": "Decimal(38,9)",
    "float": "Float64",
    "string": "String",
    "boolean": "Bool",
    "date": "Date32",
    "timestamp": "DateTime64(6)",
}

CLICKHOUSE = DialectProfile(
    name="clickhouse",
    doubled_quote_escape=True,  # the lexer reads '' as one ' (conformance-checked)
    tier="verified",
    quote_char='"',
    unquoted_case="preserve",
    reserved_words=RESERVED,
    max_table_parts=2,
    max_identifier_length=255,  # table names become file names
    count_if_style="countif",  # spelled countIf by the CountIf override
    limit_style="limit",
    cast_types={
        **{family: f"Nullable({native})" for family, native in _PARAM_TYPES.items()},
        "bytes": "Nullable(String)",
    },
    now_sql="now()",
    type_families=TYPE_FAMILIES,
    param_types=_PARAM_TYPES,
    information_schema_sql=(
        "SELECT column_name, data_type FROM information_schema.columns WHERE {where}"
    ),
    # identifiers are case-sensitive, quoted or not
    quoted_catalog_match="exact",
    unquoted_catalog_match="exact",
    quote_escapes={'"': '""', "\\": "\\\\"},
    overrides={
        A.Agg: _agg,
        A.CountIf: _count_if,
        A.SplitPrefix: _split_prefix,
        A.Cast: _cast,
        A.SubqueryFrom: _subquery,
    },
    sample_styles={"system": None, "bernoulli": None},
    # spec 19 keyhash ruling: cityHash64 is a UInt64, so no sign fold is needed
    keyhash_expr="(cityHash64({s}) % 1000000)",
    default_schema_sql="currentDatabase()",
    statement_suffix=" SETTINGS prefer_column_name_to_alias = 1",
)

__all__ = ["CLICKHOUSE"]
