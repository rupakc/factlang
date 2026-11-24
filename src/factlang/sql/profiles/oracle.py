"""Oracle Database profile (tier ``native``: SQL parsed with sqlglot in CI, not executed).

Slots: schema (owner).table (2). Unquoted names fold to upper case. ``FETCH FIRST n ROWS
ONLY`` (12c+); the part before a separator is ``SUBSTR(x, 1, INSTR(x || sep, sep) - 1)``;
the clock is ``SYS_EXTRACT_UTC(SYSTIMESTAMP)``. Column metadata comes from
``ALL_TAB_COLUMNS`` (there is no ``information_schema``), with a scale-0 ``NUMBER`` spelled
``NUMBER(p,0)`` so integer columns map to the integer family. ``SAMPLE (p) SEED (n)`` is the
``bernoulli`` method; block sampling (``SAMPLE BLOCK``) only "attempts to" repeat with a
seed, so ``system`` is FL506 (ruling, Task 15 review).

An Oracle ``DATE`` holds a time of day, so it maps to the ``timestamp`` family, and a cast to
the ``date`` family truncates it (``TRUNC(CAST(x AS DATE))``) so dates compare as dates.
"""

from __future__ import annotations

from factlang.sql import ast as A
from factlang.sql.printer import Printer
from factlang.sql.profile import DialectProfile
from factlang.sql.profiles._shared import derived_union_subquery

# Oracle SQL reserved words (SQL Language Reference, Appendix "Oracle SQL Reserved Words").
_RESERVED_WORDS = """
    access add all alter and any as asc audit between by char check cluster column comment
    compress connect create current date decimal default delete desc distinct drop else
    exclusive exists file float for from grant group having identified immediate in
    increment index initial insert integer intersect into is level like lock long
    maxextents minus mlslabel mode modify noaudit nocompress not nowait null number of
    offline on online option or order pctfree prior public raw rename resource revoke row
    rowid rownum rows select session set share size smallint start successful synonym
    sysdate table then to trigger uid union unique update user validate values varchar
    varchar2 view whenever where with
"""
RESERVED = frozenset(_RESERVED_WORDS.split())

TYPE_FAMILIES: tuple[tuple[str, str], ...] = (
    (r"number\s*\(\s*\*\s*,\s*0+\s*\)|integer|int|smallint", "integer"),
    (r"(number|numeric|decimal|dec)(\s*\(.*\))?", "decimal"),
    (r"float(\s*\(\d+\))?|binary_float|binary_double|real|double precision", "float"),
    (
        r"n?varchar2?(\s*\(.*\))?|n?char(\s*\(.*\))?|n?clob|long|character varying(\s*\(.*\))?",
        "string",
    ),
    (r"boolean", "boolean"),
    (r"date|timestamp(\s*\(\d+\))?( with( local)? time zone)?", "timestamp"),
    (r"raw(\s*\(\d+\))?|long raw|blob|bfile", "bytes"),
    (r"json", "json"),
)


def _cast(printer: Printer, x: A.Cast) -> str:
    text = f"CAST({printer.expr(x.e)} AS {printer.cast_type(x.family)})"
    return f"TRUNC({text})" if x.family == "date" else text


def _split_prefix(printer: Printer, x: A.SplitPrefix) -> str:
    e, sep = printer.expr(x.e), printer.expr(x.separator)
    return f"SUBSTR({e}, 1, INSTR({e} || {sep}, {sep}) - 1)"


ORACLE = DialectProfile(
    name="oracle",
    tier="native",
    quote_char='"',
    unquoted_case="upper",
    reserved_words=RESERVED,
    max_table_parts=2,
    max_identifier_length=128,  # bytes, 12.2+
    count_if_style="sum_case",
    limit_style="fetch",
    cast_types={  # no BOOLEAN before 23ai, no TIME, no JSON cast target
        "integer": "NUMBER(19)",
        "decimal": "NUMBER(38,9)",
        "float": "BINARY_DOUBLE",
        "string": "VARCHAR2(4000)",
        "date": "DATE",
        "timestamp": "TIMESTAMP(6)",
        "bytes": "RAW(2000)",
    },
    now_sql="SYS_EXTRACT_UTC(SYSTIMESTAMP)",
    type_families=TYPE_FAMILIES,
    param_types={
        "integer": "NUMBER(19)",
        "decimal": "NUMBER(38,9)",
        "float": "BINARY_DOUBLE",
        "string": "VARCHAR2(4000)",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "timestamp": "TIMESTAMP(6)",
    },
    information_schema_sql=(
        "SELECT column_name, data_type FROM (SELECT owner AS table_schema, table_name, "
        "column_name, CASE WHEN data_type = 'NUMBER' AND data_scale = 0 THEN 'NUMBER(' || "
        "NVL(TO_CHAR(data_precision), '*') || ',0)' ELSE data_type END AS data_type "
        "FROM all_tab_columns) fl_cols WHERE {where}"
    ),
    quoted_catalog_match="exact",
    unquoted_catalog_match="upper",
    overrides={
        A.Cast: _cast,
        A.SplitPrefix: _split_prefix,
        A.SubqueryFrom: derived_union_subquery,
    },
    sample_styles={
        "system": None,
        "bernoulli": "SAMPLE ({percent}) SEED ({seed})",
    },
    sample_seed_range=(0, 4294967295),
    # spec 19 keyhash ruling: ORA_HASH is 32-bit and non-negative; the seed lives in s
    keyhash_expr="MOD(ORA_HASH({s}, 4294967295, 0), 1000000)",
    default_schema_sql="SYS_CONTEXT('USERENV', 'CURRENT_SCHEMA')",
)

__all__ = ["ORACLE"]
