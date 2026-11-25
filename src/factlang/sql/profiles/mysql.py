"""MySQL / MariaDB profile (tier ``verified``: executed against a service container in CI,
Task 20).

Slots: database.table (2). Backtick quoting. ``||`` is logical OR unless PIPES_AS_CONCAT is
set, so the keyhash input is built with ``CONCAT``; the part before a separator is
``SUBSTRING_INDEX(x, sep, 1)``; the clock is ``UTC_TIMESTAMP()``. There is no
``TABLESAMPLE`` (both table-sample methods are FL506; keyhash is supported).
"""

from __future__ import annotations

from factlang.sql import ast as A
from factlang.sql.printer import Printer
from factlang.sql.probe import RenderError
from factlang.sql.profile import DialectProfile
from factlang.sql.profiles._shared import derived_union_subquery

# MySQL 8.0 reserved words (marked (R) in "Keywords and Reserved Words").
_RESERVED_WORDS = """
    accessible add all alter analyze and as asc asensitive before between bigint binary
    blob both by call cascade case change char character check collate column condition
    constraint continue convert create cross cube cume_dist current_date current_time
    current_timestamp current_user cursor database databases day_hour day_microsecond
    day_minute day_second dec decimal declare default delayed delete dense_rank desc
    describe deterministic distinct distinctrow div double drop dual each else elseif empty
    enclosed escaped except exists exit explain false fetch first_value float float4 float8
    for force foreign from fulltext function generated get grant group grouping groups
    having high_priority hour_microsecond hour_minute hour_second if ignore in index infile
    inner inout insensitive insert int int1 int2 int3 int4 int8 integer intersect interval
    into io_after_gtids io_before_gtids is iterate join json_table key keys kill lag
    last_value lateral lead leading leave left like limit linear lines load localtime
    localtimestamp lock long longblob longtext loop low_priority master_bind
    master_ssl_verify_server_cert match maxvalue mediumblob mediumint mediumtext middleint
    minute_microsecond minute_second mod modifies natural not no_write_to_binlog nth_value
    ntile null numeric of on optimize optimizer_costs option optionally or order out outer
    outfile over partition percent_rank precision primary procedure purge range rank read
    reads read_write real recursive references regexp release rename repeat replace require
    resignal restrict return revoke right rlike row rows row_number schema schemas
    second_microsecond select sensitive separator set show signal smallint spatial specific
    sql sqlexception sqlstate sqlwarning sql_big_result sql_calc_found_rows
    sql_small_result ssl starting stored straight_join system table terminated then
    tinyblob tinyint tinytext to trailing trigger true undo union unique unlock unsigned
    update usage use using utc_date utc_time utc_timestamp values varbinary varchar
    varcharacter varying virtual when where while window with write xor year_month zerofill
"""
RESERVED = frozenset(_RESERVED_WORDS.split())

_UNSIGNED = r"(\s+unsigned)?(\s+zerofill)?"

TYPE_FAMILIES: tuple[tuple[str, str], ...] = (
    (r"tinyint\s*\(\s*1\s*\)|bool(ean)?|bit\s*\(\s*1\s*\)", "boolean"),
    (r"(tinyint|smallint|mediumint|int|integer|bigint)(\s*\(\d+\))?" + _UNSIGNED, "integer"),
    (r"(decimal|numeric|dec)\s*\(\s*\d+\s*(,\s*0+\s*)?\)" + _UNSIGNED, "integer"),
    (r"(decimal|numeric|dec|fixed)(\s*\(.*\))?" + _UNSIGNED, "decimal"),
    (r"(float|double( precision)?|real)(\s*\(.*\))?" + _UNSIGNED, "float"),
    (
        r"(var)?char(\s*\(\d+\))?|(tiny|medium|long)?text|enum\s*\(.*\)|set\s*\(.*\)",
        "string",
    ),
    (r"date", "date"),
    (r"(datetime|timestamp)(\s*\(\d+\))?", "timestamp"),
    (r"time(\s*\(\d+\))?", "time"),
    (r"(var)?binary(\s*\(\d+\))?|(tiny|medium|long)?blob", "bytes"),
    (r"json", "json"),
)


def _split_prefix(printer: Printer, x: A.SplitPrefix) -> str:
    return f"SUBSTRING_INDEX({printer.expr(x.e)}, {printer.expr(x.separator)}, 1)"


def _keyhash(printer: Printer, x: A.KeyHash) -> str:
    template = printer.profile.keyhash_expr
    if template is None:  # pragma: no cover - the profile always declares keyhash
        raise RenderError("FL506 sample method keyhash is not supported by dialect mysql")
    parts = [printer.expr(x.seed), *(printer.expr(part) for part in x.parts)]
    return template.format(s="CONCAT(" + ", '|', ".join(parts) + ")")


MYSQL = DialectProfile(
    name="mysql",
    doubled_quote_escape=True,  # the lexer reads '' as one ' (conformance-checked)
    tier="verified",
    quote_char="`",
    unquoted_case="preserve",
    reserved_words=RESERVED,
    max_table_parts=2,
    max_identifier_length=64,
    count_if_style="sum_case",
    limit_style="limit",
    cast_types={  # CAST accepts only these targets; there is no CAST AS BOOLEAN
        "integer": "SIGNED",
        "decimal": "DECIMAL(38,9)",
        "float": "DOUBLE",
        "string": "CHAR",
        "date": "DATE",
        "timestamp": "DATETIME(6)",
        "time": "TIME(6)",
        "bytes": "BINARY",
        "json": "JSON",
    },
    now_sql="UTC_TIMESTAMP()",
    type_families=TYPE_FAMILIES,
    param_types={
        "integer": "BIGINT",
        "decimal": "DECIMAL(38,9)",
        "float": "DOUBLE",
        "string": "VARCHAR",
        "boolean": "BOOLEAN",
        "date": "DATE",
        "timestamp": "DATETIME(6)",
    },
    # column_type keeps width, sign and precision (tinyint(1), decimal(10,0), int unsigned);
    # MySQL 8 labels an unaliased information_schema column in upper case, hence the aliases
    information_schema_sql=(
        "SELECT column_name AS column_name, column_type AS data_type "
        "FROM information_schema.columns WHERE {where}"
    ),
    # lower_case_table_names and the column collation decide; LOWER() = LOWER() finds what SQL finds
    quoted_catalog_match="casefold",
    unquoted_catalog_match="casefold",
    overrides={
        A.SplitPrefix: _split_prefix,
        A.KeyHash: _keyhash,
        A.SubqueryFrom: derived_union_subquery,
    },
    sample_styles={"system": None, "bernoulli": None},
    # spec 19 keyhash ruling: 60 bits of MD5, non-negative, so a single MOD is the bucket
    keyhash_expr="MOD(CAST(CONV(SUBSTRING(MD5({s}), 1, 15), 16, 10) AS UNSIGNED), 1000000)",
    default_schema_sql="DATABASE()",
)

__all__ = ["MYSQL"]
