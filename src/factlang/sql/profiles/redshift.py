"""Amazon Redshift profile (tier ``native``: SQL parsed with sqlglot in CI, not executed).

PostgreSQL-like, with these differences: no ``FILTER`` clause (``SUM(CASE …)``), the clock is
``GETDATE()``, an integer ``AVG`` truncates (so it is taken over ``x + 0.0``), a bare
``VARCHAR`` is 256 bytes (casts use ``VARCHAR(65535)``) and there is no ``TABLESAMPLE``
(both table-sample methods are FL506; keyhash is supported).
"""

from __future__ import annotations

from factlang.sql import ast as A
from factlang.sql.profile import DialectProfile
from factlang.sql.profiles._shared import derived_union_subquery, widened_aggregates

# Redshift reserved words (Database Developer Guide, "Reserved words").
_RESERVED_WORDS = """
    aes128 aes256 all allowoverwrite analyse analyze and any array as asc authorization
    az64 backup between binary blanksasnull both bytedict bzip2 case cast check collate
    column constraint create credentials cross current_date current_time current_timestamp
    current_user current_user_id default deferrable deflate defrag delta delta32k desc
    disable distinct do else emptyasnull enable encode encrypt encryption end except
    explicit false for foreign freeze from full globaldict256 globaldict64k grant group
    gzip having identity ignore ilike in initially inner intersect interval into is isnull
    join language leading left like limit localtime localtimestamp lun luns lzo lzop minus
    mostly16 mostly32 mostly8 natural new not notnull null nulls off offline offset oid old
    on only open or order outer overlaps parallel partition percent permissions pivot
    placing primary raw readratio recover references rejectlog resort respect restore
    right select session_user similar snapshot some sysdate system table tag tdes text255
    text32k then timestamp to top trailing true truncatecolumns union unique unnest unpivot
    user using verbose wallet when where with without
"""
RESERVED = frozenset(_RESERVED_WORDS.split())

TYPE_FAMILIES: tuple[tuple[str, str], ...] = (
    (r"smallint|integer|bigint|int[248]?", "integer"),
    (r"(numeric|decimal)(\s*\(.*\))?", "decimal"),
    (r"real|double precision|float[48]?", "float"),
    (
        r"character varying(\s*\(.*\))?|varchar(\s*\(.*\))?|character(\s*\(.*\))?"
        r"|char(\s*\(.*\))?|bpchar|text|name",
        "string",
    ),
    (r"bool(ean)?", "boolean"),
    (r"date", "date"),
    (r"timestamp( with(out)? time zone)?|timestamptz", "timestamp"),
    (r"time( with(out)? time zone)?|timetz", "time"),
    (r"varbyte(\s*\(.*\))?|binary varying(\s*\(.*\))?|varbinary(\s*\(.*\))?", "bytes"),
    (r"super", "json"),
)

_PARAM_TYPES = {
    "integer": "BIGINT",
    "decimal": "NUMERIC(38,9)",
    "float": "DOUBLE PRECISION",
    "string": "VARCHAR(65535)",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
}

REDSHIFT = DialectProfile(
    name="redshift",
    doubled_quote_escape=True,  # the lexer reads '' as one ' (conformance-checked)
    tier="native",
    quote_char='"',
    unquoted_case="lower",
    reserved_words=RESERVED,
    max_table_parts=3,
    max_identifier_length=127,  # bytes
    count_if_style="sum_case",
    limit_style="limit",
    cast_types={
        **_PARAM_TYPES,
        "time": "TIME",
        "bytes": "VARBYTE",
        "json": "SUPER",
    },
    now_sql="GETDATE()",
    type_families=TYPE_FAMILIES,
    param_types=_PARAM_TYPES,
    # svv_all_columns covers every database (cross-database queries, datashares), local and
    # external tables; numeric precision/scale are spelled out so numeric(p,0) is an integer
    information_schema_sql=(
        "SELECT column_name, data_type FROM (SELECT database_name AS table_catalog, "
        "schema_name AS table_schema, table_name, column_name, CASE WHEN data_type = 'numeric' "
        "AND numeric_scale IS NOT NULL THEN 'numeric(' || "
        "CAST(numeric_precision AS VARCHAR) || ',' || CAST(numeric_scale AS VARCHAR) || ')' "
        "ELSE data_type END AS data_type FROM svv_all_columns) fl_cols "
        "WHERE {where}"
    ),
    # with the default enable_case_sensitive_identifier = false, quoted names fold too
    quoted_catalog_match="lower",
    unquoted_catalog_match="lower",
    overrides={A.Agg: widened_aggregates(), A.SubqueryFrom: derived_union_subquery},
    sample_styles={"system": None, "bernoulli": None},
    # spec 19 keyhash ruling: 60 bits of MD5, non-negative, so a single MOD is the bucket
    keyhash_expr="MOD(STRTOL(SUBSTRING(MD5({s}), 1, 15), 16), 1000000)",
    cast_params=True,  # every bind parameter is typed in the SQL text
    default_schema_sql="current_schema()",
    default_catalog_sql="current_database()",
)

__all__ = ["REDSHIFT"]
