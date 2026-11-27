"""Values crossing the browser bridge keep their exact type and value (site spec 5.2, 6).

Results travel DuckDB -> tagged JSON -> Python; parameters travel Python -> tagged JSON ->
``CAST(? AS <type>)`` in DuckDB. Every row below goes through the real wire format
(``json.dumps``/``json.loads``) and is compared with what Python ``duckdb`` returns natively.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import re
from decimal import Decimal
from types import ModuleType
from typing import Any

import pytest

duckdb = pytest.importorskip("duckdb")

# (SQL expression, DuckDB type DESCRIBE reports, wire tag)
RESULT_CASES = [
    ("CAST('12.34' AS DECIMAL(10, 2))", "DECIMAL(10,2)", "decimal"),
    ("CAST('-0.05' AS DECIMAL(10, 2))", "DECIMAL(10,2)", "decimal"),
    ("CAST('0' AS DECIMAL(38, 9))", "DECIMAL(38,9)", "decimal"),
    (
        "CAST('12345678901234567890123456789.123456789' AS DECIMAL(38, 9))",
        "DECIMAL(38,9)",
        "decimal",
    ),
    ("CAST('209000' AS DECIMAL(38, 0))", "DECIMAL(38,0)", "decimal"),
    ("CAST(9223372036854775807 AS BIGINT)", "BIGINT", "integer"),
    ("CAST(-9223372036854775808 AS BIGINT)", "BIGINT", "integer"),
    ("CAST(42 AS INTEGER)", "INTEGER", "integer"),
    ("CAST('1267650600228229401496703205376' AS HUGEINT)", "HUGEINT", "integer"),
    ("CAST('-1267650600228229401496703205376' AS HUGEINT)", "HUGEINT", "integer"),
    ("SUM(CAST(x AS BIGINT)) FROM range(3) AS t(x)", "HUGEINT", "integer"),
    ("CAST(0.1 AS DOUBLE)", "DOUBLE", "float"),
    ("CAST(1e300 AS DOUBLE)", "DOUBLE", "float"),
    ("CAST(-2.5e-12 AS DOUBLE)", "DOUBLE", "float"),
    ("CAST('inf' AS DOUBLE)", "DOUBLE", "float"),
    ("CAST('-inf' AS DOUBLE)", "DOUBLE", "float"),
    ("CAST(DATE '2024-02-29' AS DATE)", "DATE", "date"),
    ("CAST('2024-02-29 13:45:01.123456' AS TIMESTAMP)", "TIMESTAMP", "timestamp"),
    ("CAST('1999-12-31 23:59:59' AS TIMESTAMP)", "TIMESTAMP", "timestamp"),
    ("TRUE", "BOOLEAN", "boolean"),
    ("FALSE", "BOOLEAN", "boolean"),
    ("'it''s \"quoted\" — ünïcode'", "VARCHAR", "string"),
    ("CAST(NULL AS INTEGER)", "INTEGER", "null"),
    ("CAST(NULL AS DECIMAL(10, 2))", "DECIMAL(10,2)", "null"),
]


def _wire(value: Any) -> Any:
    return json.loads(json.dumps(value))


@pytest.mark.parametrize(
    ("expr", "duck_type", "tag"), RESULT_CASES, ids=[c[0] for c in RESULT_CASES]
)
def test_result_cells_round_trip(
    glue: ModuleType, bridge: ModuleType, expr: str, duck_type: str, tag: str
) -> None:
    con = duckdb.connect()
    sql = f"SELECT {expr} AS v" if " FROM " not in expr else f"SELECT {expr}"
    native = con.execute(sql).fetchone()[0]
    result = _wire(bridge.query(con, sql, []))
    assert result["columns"][0][1] == duck_type
    (cell,) = result["rows"][0]
    assert cell["t"] == tag
    if tag in ("decimal", "integer", "date", "timestamp"):
        assert isinstance(cell["v"], str), "exact values cross as strings"
    decoded = glue.decode_value(cell)
    assert type(decoded) is type(native)
    assert decoded == native
    if isinstance(native, Decimal):
        assert decoded.as_tuple() == native.as_tuple(), "same digits and scale"


def test_nan_crosses_as_text(glue: ModuleType, bridge: ModuleType) -> None:
    con = duckdb.connect()
    (cell,) = _wire(bridge.query(con, "SELECT CAST('nan' AS DOUBLE) AS v", []))["rows"][0]
    assert cell == {"t": "float", "v": "nan"}
    assert math.isnan(glue.decode_value(cell))


@pytest.mark.parametrize(
    ("duck_type", "kind"),
    [
        ("BIGINT", "integer"),
        ("HUGEINT", "integer"),
        ("UBIGINT", "integer"),
        ("TINYINT", "integer"),
        ("DECIMAL(38,0)", "decimal"),
        ("DECIMAL(10,2)", "decimal"),
        ("DOUBLE", "float"),
        ("FLOAT", "float"),
        ("VARCHAR", "string"),
        ("BOOLEAN", "boolean"),
        ("DATE", "date"),
        ("TIMESTAMP", "timestamp"),
        ("TIMESTAMP WITH TIME ZONE", "timestamp"),
        ("TIMESTAMP_NS", "timestamp"),
        ("TIME", "time"),
        ("INTEGER[]", "other"),
        ("STRUCT(a INTEGER)", "other"),
        ("INTERVAL", "other"),
        ("BLOB", "other"),
    ],
)
def test_value_kind(glue: ModuleType, duck_type: str, kind: str) -> None:
    assert glue.value_kind(duck_type) == kind


PARAM_CASES = [
    (7, "integer", {"t": "integer", "v": "7"}, "BIGINT"),
    (2**70, "integer", {"t": "integer", "v": str(2**70)}, "HUGEINT"),
    (Decimal("2.5"), "decimal", {"t": "decimal", "v": "2.5"}, "DECIMAL(2,1)"),
    (Decimal("-0.001"), "decimal", {"t": "decimal", "v": "-0.001"}, "DECIMAL(3,3)"),
    (Decimal("1E+3"), "decimal", {"t": "decimal", "v": "1000"}, "DECIMAL(4,0)"),
    (0.1, "float", {"t": "float", "v": "0.1"}, "DOUBLE"),
    (float("inf"), "float", {"t": "float", "v": "inf"}, "DOUBLE"),
    ("it's", "string", {"t": "string", "v": "it's"}, "VARCHAR"),
    (True, "boolean", {"t": "boolean", "v": True}, "BOOLEAN"),
    (dt.date(2024, 2, 29), "date", {"t": "date", "v": "2024-02-29"}, "DATE"),
    (
        dt.datetime(2024, 1, 1, 12, 30, 0, 5, tzinfo=dt.timezone.utc),
        "timestamp",
        {"t": "timestamp", "v": "2024-01-01T12:30:00.000005"},
        "TIMESTAMP",
    ),
    (
        dt.datetime(2024, 1, 1, 14, 0, tzinfo=dt.timezone(dt.timedelta(hours=2))),
        "timestamp",
        {"t": "timestamp", "v": "2024-01-01T12:00:00"},
        "TIMESTAMP",
    ),
    (None, "string", {"t": "null", "v": None}, "VARCHAR"),
]


@pytest.mark.parametrize(("value", "family", "tagged", "sql_type"), PARAM_CASES)
def test_params_bind_with_explicit_casts(
    glue: ModuleType, value: Any, family: str, tagged: dict[str, Any], sql_type: str
) -> None:
    assert glue.encode_param(value, family) == tagged
    assert glue.param_sql_type(value, family) == sql_type
    con = duckdb.connect()
    wire = _wire(tagged)
    (got,) = con.execute(f"SELECT CAST(? AS {sql_type})", [wire["v"]]).fetchone()
    if value is None:
        assert got is None
    elif isinstance(value, dt.datetime):
        assert got == value.astimezone(dt.timezone.utc).replace(tzinfo=None)
    else:
        assert got == value


def test_encode_param_infers_the_family(glue: ModuleType) -> None:
    assert glue.encode_param(3) == {"t": "integer", "v": "3"}
    assert glue.encode_param(Decimal("1.50")) == {"t": "decimal", "v": "1.50"}
    assert glue.encode_param(False) == {"t": "boolean", "v": False}
    assert glue.encode_param("x") == {"t": "string", "v": "x"}
    assert glue.encode_param(dt.date(2020, 1, 2)) == {"t": "date", "v": "2020-01-02"}


def test_decode_rejects_an_unknown_tag(glue: ModuleType) -> None:
    with pytest.raises(ValueError, match="unknown value tag"):
        glue.decode_value({"t": "mystery", "v": "1"})


def test_other_values_pass_through_as_text(glue: ModuleType) -> None:
    assert glue.decode_value({"t": "other", "v": "[1, 2]"}) == "[1, 2]"
    assert glue.decode_value({"t": "time", "v": "13:45:01.000250"}) == dt.time(13, 45, 1, 250)


def test_infinite_dates_decode_as_python_duckdb_returns_them(glue: ModuleType) -> None:
    con = duckdb.connect()
    native = con.execute(
        "SELECT 'infinity'::DATE, '-infinity'::DATE, 'infinity'::TIMESTAMP, '-infinity'::TIMESTAMP"
    ).fetchone()
    decoded = (
        glue.decode_value({"t": "date", "v": "infinity"}),
        glue.decode_value({"t": "date", "v": "-infinity"}),
        glue.decode_value({"t": "timestamp", "v": "infinity"}),
        glue.decode_value({"t": "timestamp", "v": "-infinity"}),
    )
    assert decoded == native
    assert decoded == (dt.date.max, dt.date.min, dt.datetime.max, dt.datetime.min)


def test_untyped_null_columns_decode_as_null(glue: ModuleType) -> None:
    assert glue.value_kind('"NULL"') == "null"
    assert glue.value_kind("NULL") == "null"
    assert glue.decode_value({"t": "null", "v": None}) is None


def test_engine_js_reads_the_value_kind_table_from_glue(
    glue: ModuleType, bridge: ModuleType
) -> None:
    """One kind table for both sides: engine.js compiles glue's VALUE_KINDS_JSON."""
    table = json.loads(glue.VALUE_KINDS_JSON)
    assert [tuple(k) for k in table] == list(glue._KINDS)
    for pattern, _ in table:
        # only syntax Python and JavaScript read the same way
        assert not re.search(r"\(\?[P<=!#]|\\[AZzGh]|\(\?[aiLmsux]", pattern), pattern
    engine = (bridge.ROOT / "site-playground" / "engine.js").read_text(encoding="utf-8")
    assert 'globals.get("VALUE_KINDS_JSON")' in engine
    assert "TINYINT" not in engine and "HUGEINT|" not in engine, "no second kind table in JS"
