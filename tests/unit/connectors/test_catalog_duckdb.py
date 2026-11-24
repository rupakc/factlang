"""WarehouseCatalog over a real DuckDB (spec 9.2, 6.1)."""

from __future__ import annotations

from typing import Any

import pytest

from factlang.connectors import (
    CallableConnector,
    DBAPIConnector,
    ExecutionError,
    QueryResult,
    WarehouseCatalog,
)
from factlang.semantics.catalog import CachedCatalog, Catalog
from factlang.semantics.refs import Ident, TableRef
from factlang.sql import RenderedProbe

duckdb = pytest.importorskip("duckdb")


def ref(*parts: str, quoted: bool = False) -> TableRef:
    idents = [Ident(p, quoted) for p in parts]
    while len(idents) < 3:
        idents.insert(0, None)  # type: ignore[arg-type]
    return TableRef(idents[0], idents[1], idents[2])  # type: ignore[arg-type]


@pytest.fixture()
def con() -> Any:
    db = duckdb.connect()
    db.execute(
        """
        CREATE TABLE orders(id INT, total DECIMAL(10,2), whole DECIMAL(12,0), status VARCHAR,
                            paid BOOLEAN, created_at TIMESTAMP, created_tz TIMESTAMPTZ,
                            day DATE, tags VARCHAR[], payload JSON, ratio DOUBLE, raw BLOB,
                            "Mixed Case" BIGINT);
        CREATE SCHEMA shop;
        CREATE TABLE shop.customers(id BIGINT, name VARCHAR);
        """
    )
    yield db
    db.close()


class Counting:
    """Wraps a connector and counts executed probes."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.dialect = inner.dialect
        self.paramstyle = inner.paramstyle
        self.probes: list[RenderedProbe] = []

    def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
        self.probes.append(probe)
        result: QueryResult = self.inner.execute(probe, timeout_s=timeout_s)
        return result


def test_is_a_catalog(con: Any) -> None:
    cat: Catalog = WarehouseCatalog(DBAPIConnector(con, "duckdb"))
    assert cat.table_exists(ref("orders")) is True


def test_columns_are_type_families(con: Any) -> None:
    cols = WarehouseCatalog(DBAPIConnector(con, "duckdb")).columns(ref("orders"))
    assert cols == {
        "id": "integer",
        "total": "decimal",
        "whole": "integer",
        "status": "string",
        "paid": "boolean",
        "created_at": "timestamp",
        "created_tz": "timestamp",
        "day": "date",
        "tags": "array",
        "payload": "json",
        "ratio": "float",
        "raw": "bytes",
        "Mixed Case": "integer",
    }


def test_native_types_are_kept(con: Any) -> None:
    native = WarehouseCatalog(DBAPIConnector(con, "duckdb")).native_columns(ref("orders"))
    assert native is not None
    assert native["total"] == "DECIMAL(10,2)"


def test_missing_table_is_false_and_no_columns(con: Any) -> None:
    cat = WarehouseCatalog(DBAPIConnector(con, "duckdb"))
    assert cat.table_exists(ref("nope")) is False
    assert cat.columns(ref("nope")) is None


def test_schema_qualified_and_default_schema(con: Any) -> None:
    cat = WarehouseCatalog(DBAPIConnector(con, "duckdb"))
    assert cat.table_exists(ref("shop", "customers")) is True
    # unqualified resolves against current_schema() only, like the same name in SQL would
    assert cat.table_exists(ref("customers")) is False
    assert cat.columns(ref("shop", "customers")) == {"id": "integer", "name": "string"}


def test_case_insensitive_unquoted(con: Any) -> None:
    cat = WarehouseCatalog(DBAPIConnector(con, "duckdb"))
    assert cat.table_exists(ref("ORDERS")) is True


def test_hostile_name_is_bound_not_spliced(con: Any) -> None:
    counting = Counting(DBAPIConnector(con, "duckdb"))
    cat = WarehouseCatalog(counting)
    assert cat.table_exists(ref("orders' OR '1'='1", quoted=True)) is False
    assert "OR '1'='1" not in counting.probes[0].sql_template


def test_one_query_per_table_never_enumerates(con: Any) -> None:
    counting = Counting(DBAPIConnector(con, "duckdb"))
    cat = CachedCatalog(WarehouseCatalog(counting))
    cat.columns(ref("orders"))
    cat.columns(ref("orders"))
    assert len(counting.probes) == 1
    probe = counting.probes[0]
    assert probe.plan is not None and type(probe.plan).__name__ == "MetadataQuery"
    assert "table_name" in probe.sql_template


def test_execution_error_is_unknown_and_recorded() -> None:
    def fn(sql: str, params: object) -> list[dict[str, Any]]:
        raise RuntimeError("permission denied for information_schema")

    cat = WarehouseCatalog(CallableConnector(fn, "duckdb"))  # type: ignore[arg-type]
    assert cat.table_exists(ref("orders")) is None
    assert cat.columns(ref("orders")) is None
    assert cat.errors and isinstance(cat.errors[-1], ExecutionError)
    assert cat.errors[-1].code == "FL700"


def test_unrenderable_ref_is_unknown() -> None:
    cat = WarehouseCatalog(CallableConnector(lambda s, p: [], "postgres"))
    # postgres allows at most 3 parts; a NUL byte is not representable as an identifier
    assert cat.table_exists(ref("bad\x00name", quoted=True)) is None
    assert cat.errors


def test_callable_rows_are_mapped_to_families() -> None:
    rows = [{"column_name": "id", "data_type": "INT"}]
    cat = WarehouseCatalog(CallableConnector(lambda s, p: rows, "duckdb"))
    cat_rows = cat.columns(ref("orders"))
    assert cat_rows == {"id": "integer"}


def test_rows_with_upper_case_keys_are_read() -> None:
    # Snowflake / Oracle return upper-case column labels
    rows = [{"COLUMN_NAME": "ID", "DATA_TYPE": "NUMBER(38,0)"}]
    cat = WarehouseCatalog(CallableConnector(lambda s, p: rows, "snowflake", paramstyle="qmark"))
    assert cat.columns(ref("orders")) == {"ID": "integer"}


def test_malformed_rows_are_unknown() -> None:
    cat = WarehouseCatalog(CallableConnector(lambda s, p: [{"x": 1}], "duckdb"))
    assert cat.columns(ref("orders")) is None
    assert cat.errors
