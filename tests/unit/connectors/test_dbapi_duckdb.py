"""DBAPIConnector against a real DuckDB and small PEP 249 fakes (spec 9.1, 18 R7/R8)."""

from __future__ import annotations

import datetime as dt
import sqlite3
import sys
import time
import types
from decimal import Decimal
from typing import Any

import pytest

from factlang.connectors import DBAPIConnector, ExecutionError, run_metadata
from factlang.ir import ColumnAggregate, Metric, ValueMembership
from factlang.semantics.filters import FCompare, Param
from factlang.semantics.refs import Ident, TableRef
from factlang.sql import RenderedProbe, get_profile, render

duckdb = pytest.importorskip("duckdb")

DUCK = get_profile("duckdb")
assert DUCK is not None
ORDERS = TableRef(None, None, Ident("orders"))


@pytest.fixture()
def con() -> Any:
    db = duckdb.connect()
    db.execute(
        """
        CREATE TABLE orders(id INT, status VARCHAR, total DECIMAL(10,2), created_at TIMESTAMP);
        INSERT INTO orders VALUES (1, 'paid', 5.00, '2026-01-01'), (2, 'open', 7.50, NULL),
          (3, 'paid', NULL, '2026-01-03'), (4, '50%', 1.25, NULL);
        """
    )
    yield db
    db.close()


def count_where(status: str) -> RenderedProbe:
    plan = ColumnAggregate(
        ORDERS,
        Ident("total"),
        (Metric.ROW_COUNT, Metric.NON_NULL, Metric.SUM),
        filter=FCompare(Ident("status"), "=", Param("p1", status, "string")),
    )
    return render(plan, DUCK)


# -- paramstyle ------------------------------------------------------------------------------


def test_duckdb_paramstyle_detected(con: Any) -> None:
    c = DBAPIConnector(con, "duckdb")
    assert c.paramstyle == "qmark"
    assert c.dialect == "duckdb"


def test_sqlite_paramstyle_detected() -> None:
    assert DBAPIConnector(sqlite3.connect(":memory:"), "sqlite").paramstyle == "qmark"


def test_explicit_paramstyle_wins_and_is_validated(con: Any) -> None:
    assert DBAPIConnector(con, "duckdb", paramstyle="numeric").paramstyle == "numeric"
    with pytest.raises(ValueError):
        DBAPIConnector(con, "duckdb", paramstyle="dollar")


def test_unknown_driver_defaults_to_qmark() -> None:
    class Conn:
        pass

    Conn.__module__ = "no_such_driver_xyz.core"
    assert DBAPIConnector(Conn(), "duckdb").paramstyle == "qmark"


# -- execution against DuckDB ------------------------------------------------------------------


def test_executes_a_rendered_probe(con: Any) -> None:
    result = DBAPIConnector(con, "duckdb").execute(count_where("paid"), timeout_s=None)
    assert result.bytes_processed is None
    assert result.rows == [{"row_count": 2, "non_null": 1, "sum_value": Decimal("5.00")}]


def test_hostile_literal_is_bound(con: Any) -> None:
    result = DBAPIConnector(con, "duckdb").execute(count_where("x' OR '1'='1"), timeout_s=None)
    assert result.rows[0]["row_count"] == 0


def test_row_set_probe(con: Any) -> None:
    plan = ValueMembership(ORDERS, Ident("status"), ("paid",))
    rows = DBAPIConnector(con, "duckdb").execute(render(plan, DUCK), timeout_s=5).rows
    assert rows and all(isinstance(r, dict) for r in rows)


def test_server_now_is_naive_utc_without_pytz_or_tzdata(con: Any) -> None:
    """server_now is a naive UTC TIMESTAMP, so fetching it needs neither pytz (3.10) nor
    tzdata (Windows), whatever the session time zone."""
    con.execute("SET TimeZone = 'America/New_York'")
    plan = ColumnAggregate(ORDERS, None, (Metric.NOW,))
    before = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    rows = DBAPIConnector(con, "duckdb").execute(render(plan, DUCK), timeout_s=None).rows
    after = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    now = rows[0]["server_now"]
    assert isinstance(now, dt.datetime) and now.tzinfo is None
    assert before - dt.timedelta(seconds=5) <= now <= after + dt.timedelta(seconds=5)


def test_sql_error_is_fl700_and_chained(con: Any) -> None:
    con.execute("DROP TABLE orders")
    with pytest.raises(ExecutionError) as info:
        DBAPIConnector(con, "duckdb").execute(count_where("paid"), timeout_s=None)
    assert info.value.code == "FL700"
    assert info.value.__cause__ is not None


def test_timeout_interrupts_and_is_fl702(con: Any) -> None:
    probe = RenderedProbe(
        "SELECT sum(hash(i)) AS s FROM range(100000000000) t(i)",
        (),
        count_where("x").arity,
        count_where("x").plan,
        "duckdb",
        "verified",
    )
    started = time.monotonic()
    with pytest.raises(ExecutionError) as info:
        DBAPIConnector(con, "duckdb").execute(probe, timeout_s=0.3)
    assert info.value.code == "FL702"
    assert time.monotonic() - started < 30
    # the connection is usable afterwards
    assert DBAPIConnector(con, "duckdb").execute(count_where("paid"), timeout_s=5).rows


def test_fast_query_within_timeout(con: Any) -> None:
    c = DBAPIConnector(con, "duckdb")
    for _ in range(3):
        assert c.execute(count_where("paid"), timeout_s=10).rows[0]["row_count"] == 2


def test_run_metadata_information_schema(con: Any) -> None:
    result = run_metadata(
        DBAPIConnector(con, "duckdb"),
        "SELECT table_name FROM information_schema.tables WHERE table_name = {{t}}",
        (Param("t", "orders", "string"),),
    )
    assert result.rows == [{"table_name": "orders"}]


def test_run_metadata_show_and_describe(con: Any) -> None:
    c = DBAPIConnector(con, "duckdb")
    assert {"name": "orders"} in run_metadata(c, "SHOW TABLES").rows
    names = [r["column_name"] for r in run_metadata(c, "DESCRIBE orders").rows]
    assert names == ["id", "status", "total", "created_at"]


def test_run_metadata_refuses_dml_and_leaves_data_alone(con: Any) -> None:
    c = DBAPIConnector(con, "duckdb")
    for sql in ("DELETE FROM orders", "SELECT 1; DROP TABLE orders", "CREATE TABLE x (a INT)"):
        with pytest.raises(ExecutionError) as info:
            run_metadata(c, sql)
        assert info.value.code == "FL703"
    assert con.execute("SELECT count(*) FROM orders").fetchone() == (4,)


# -- PEP 249 fakes: format styles, missing description, timeout-shaped driver errors ----------


class FakeCursor:
    def __init__(self, conn: FakeConn) -> None:
        self.conn = conn
        self.description: Any = [(label,) for label in conn.labels]
        self.closed = False

    def execute(self, sql: str, params: Any) -> None:
        self.conn.log.append((sql, params))
        if self.conn.raise_exc is not None:
            raise self.conn.raise_exc
        if self.conn.no_description:
            self.description = None

    def fetchall(self) -> list[tuple[Any, ...]]:
        return [(1, "50.0")]

    def close(self) -> None:
        self.closed = True


class FakeConn:
    def __init__(self) -> None:
        self.log: list[tuple[str, Any]] = []
        self.raise_exc: BaseException | None = None
        self.no_description = False
        self.cursors: list[FakeCursor] = []
        self.labels: tuple[str, ...] = ("n", "pct")

    def cursor(self) -> FakeCursor:
        cur = FakeCursor(self)
        self.cursors.append(cur)
        return cur


def test_pyformat_doubles_percent_and_passes_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.ModuleType("fakepg")
    mod.paramstyle = "pyformat"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fakepg", mod)
    cls = type("Connection", (FakeConn,), {"__module__": "fakepg.connection"})
    conn = cls()
    c = DBAPIConnector(conn, "postgres")
    assert c.paramstyle == "pyformat"
    probe = RenderedProbe(
        "SELECT 1 AS n, '50%' AS pct WHERE x = {{a}}",
        (Param("a", "v", "string"),),
        count_where("x").arity,
        count_where("x").plan,
        "postgres",
        "verified",
    )
    result = c.execute(probe, timeout_s=None)
    sql, params = conn.log[0]
    assert sql == "SELECT 1 AS n, '50%%' AS pct WHERE x = %(a)s"
    assert params == {"a": "v"}
    assert result.rows == [{"n": 1, "pct": "50.0"}]  # numeric strings pass through as returned
    assert conn.cursors[0].closed


def test_params_are_always_passed_even_when_empty() -> None:
    conn = FakeConn()
    probe = RenderedProbe(
        "SELECT 1 AS n", (), count_where("x").arity, count_where("x").plan, "duckdb", "verified"
    )
    DBAPIConnector(conn, "duckdb", paramstyle="format").execute(probe, timeout_s=None)
    assert conn.log == [("SELECT 1 AS n", [])]


def test_no_description_means_no_rows() -> None:
    conn = FakeConn()
    conn.no_description = True
    probe = RenderedProbe(
        "SELECT 1 AS n", (), count_where("x").arity, count_where("x").plan, "duckdb", "verified"
    )
    assert DBAPIConnector(conn, "duckdb").execute(probe, timeout_s=None).rows == []


@pytest.mark.parametrize(
    "exc",
    [
        type("QueryCanceled", (Exception,), {})("canceling statement due to user request"),
        RuntimeError("canceling statement due to statement timeout"),
        TimeoutError("socket"),
        RuntimeError("Query timed out after 5s"),
    ],
)
def test_driver_timeouts_are_fl702(exc: BaseException) -> None:
    conn = FakeConn()
    conn.raise_exc = exc
    with pytest.raises(ExecutionError) as info:
        DBAPIConnector(conn, "duckdb").execute(count_where("x"), timeout_s=None)
    assert info.value.code == "FL702"
    assert info.value.__cause__ is exc
    assert conn.cursors[0].closed


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError('invalid value for parameter "statement_timeout": "abc"'),
        RuntimeError('unrecognized configuration parameter "statement_timeout"'),
        TimeoutError("connection timed out"),
        type("ConnectTimeout", (Exception,), {})("timed out after 10s"),
        RuntimeError("could not connect to server: Connection timed out"),
        RuntimeError("login timeout expired"),
        RuntimeError("interrupted system call"),
        RuntimeError("time out of range"),
        RuntimeError("column timeout does not exist"),
    ],
)
def test_non_query_timeouts_are_fl700(exc: BaseException) -> None:
    """Negative control: only a query running out of time is FL702."""
    conn = FakeConn()
    conn.raise_exc = exc
    with pytest.raises(ExecutionError) as info:
        DBAPIConnector(conn, "duckdb").execute(count_where("x"), timeout_s=None)
    assert info.value.code == "FL700", exc


def test_probe_labels_are_lower_cased() -> None:
    conn = FakeConn()
    conn.labels = ("ROW_COUNT", "SERVER_NOW")
    rows = DBAPIConnector(conn, "snowflake").execute(count_where("x"), timeout_s=None).rows
    assert rows == [{"row_count": 1, "server_now": "50.0"}]


def test_metadata_labels_are_kept_as_returned() -> None:
    conn = FakeConn()
    conn.labels = ("TABLE_NAME", "Kind")
    rows = run_metadata(DBAPIConnector(conn, "snowflake"), "SHOW TABLES").rows
    assert rows == [{"TABLE_NAME": 1, "Kind": "50.0"}]


def test_other_driver_errors_are_fl700() -> None:
    conn = FakeConn()
    conn.raise_exc = ValueError("syntax error at or near")
    with pytest.raises(ExecutionError) as info:
        DBAPIConnector(conn, "duckdb").execute(count_where("x"), timeout_s=None)
    assert info.value.code == "FL700"


def test_keyboard_interrupt_is_not_swallowed() -> None:
    conn = FakeConn()
    conn.raise_exc = KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        DBAPIConnector(conn, "duckdb").execute(count_where("x"), timeout_s=None)


def test_trino_dbapi_paramstyle(monkeypatch: pytest.MonkeyPatch) -> None:
    """R7: Trino is covered by DBAPIConnector over ``trino.dbapi`` (paramstyle qmark)."""
    trino = types.ModuleType("trino")
    dbapi = types.ModuleType("trino.dbapi")
    dbapi.paramstyle = "qmark"  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "trino", trino)
    monkeypatch.setitem(sys.modules, "trino.dbapi", dbapi)
    cls = type("Connection", (FakeConn,), {"__module__": "trino.dbapi"})
    conn = cls()
    c = DBAPIConnector(conn, "trino")
    assert c.paramstyle == "qmark"
    c.execute(count_where("paid"), timeout_s=None)
    sql, params = conn.log[0]
    assert "?" in sql and params == ["paid"]
