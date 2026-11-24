"""DBAPIConnector never shares a connection between threads unless the driver allows it.

``Checker`` runs probes on a thread pool (``concurrency=4``). PEP 249 ``threadsafety`` < 2
means threads may not share a connection: pymysql then interleaves two statements on one
socket ("Packet sequence number wrong", found on MySQL 8.4 by the Task 20 engine matrix), and
pyodbc is likewise level 1. The connector serialises statements for such drivers (and for
drivers that do not say), and runs them concurrently for level 2 and 3 drivers.
"""

from __future__ import annotations

import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import pytest

from factlang.connectors import DBAPIConnector
from factlang.ir import ColumnAggregate, Metric
from factlang.semantics.refs import Ident, TableRef
from factlang.sql import get_profile, render

PROFILE = get_profile("duckdb")
assert PROFILE is not None
PROBE = render(
    ColumnAggregate(TableRef(None, None, Ident("t")), None, (Metric.ROW_COUNT,)), PROFILE
)


class Cursor:
    def __init__(self, conn: Conn) -> None:
        self.conn = conn
        self.description: Any = None

    def execute(self, sql: str, params: Any) -> None:
        if self.conn.fail:
            raise RuntimeError("no such table")
        with self.conn.guard:
            self.conn.active += 1
            self.conn.peak = max(self.conn.peak, self.conn.active)
        time.sleep(0.02)
        with self.conn.guard:
            self.conn.active -= 1
        self.description = (("row_count",),)

    def fetchall(self) -> list[tuple[int]]:
        return [(1,)]

    def close(self) -> None:
        pass


class Conn:
    def __init__(self) -> None:
        self.guard = threading.Lock()
        self.active = 0
        self.peak = 0
        self.fail = False

    def cursor(self) -> Cursor:
        return Cursor(self)


def _driver(monkeypatch: pytest.MonkeyPatch, name: str, threadsafety: int | None) -> Conn:
    mod = types.ModuleType(name)
    mod.paramstyle = "qmark"  # type: ignore[attr-defined]
    if threadsafety is not None:
        mod.threadsafety = threadsafety  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, name, mod)
    cls = type("Connection", (Conn,), {"__module__": f"{name}.connections"})
    conn: Conn = cls()
    return conn


def _hammer(connector: DBAPIConnector, n: int = 8) -> None:
    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(lambda _: connector.execute(PROBE, timeout_s=None), range(n)))
    assert all(r.rows == [{"row_count": 1}] for r in results)


@pytest.mark.parametrize("level", [0, 1, None])
def test_level_0_1_or_undeclared_driver_is_serialised(
    monkeypatch: pytest.MonkeyPatch, level: int | None
) -> None:
    conn = _driver(monkeypatch, f"fake_ts_{level}", level)
    _hammer(DBAPIConnector(conn, "duckdb"))
    assert conn.peak == 1


@pytest.mark.parametrize("level", [2, 3])
def test_level_2_driver_with_cursor_cancel_runs_concurrently(
    monkeypatch: pytest.MonkeyPatch, level: int
) -> None:
    conn = _driver(monkeypatch, f"fake_ts_{level}", level)
    conn.cursor = lambda: CancellingCursor(conn)  # type: ignore[method-assign]
    _hammer(DBAPIConnector(conn, "duckdb"))
    assert conn.peak > 1


def test_a_failing_statement_releases_the_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _driver(monkeypatch, "fake_ts_fail", 1)
    connector = DBAPIConnector(conn, "duckdb")
    conn.fail = True
    with pytest.raises(Exception, match="no such table"):
        connector.execute(PROBE, timeout_s=None)
    conn.fail = False
    _hammer(connector, 2)  # would deadlock if the failed statement had kept the lock
    assert conn.peak == 1


def test_clickhouse_connect_is_serialised_despite_declaring_level_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clickhouse-connect's DB-API says ``threadsafety = 2`` but refuses a second query on a
    session while one runs ("Attempt to execute concurrent queries within the same
    session"), found on ClickHouse 24.8 by the Task 20 engine matrix."""
    monkeypatch.setitem(sys.modules, "clickhouse_connect", types.ModuleType("clickhouse_connect"))
    mod = types.ModuleType("clickhouse_connect.dbapi")
    mod.paramstyle = "pyformat"  # type: ignore[attr-defined]
    mod.threadsafety = 2  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "clickhouse_connect.dbapi", mod)
    cls = type("Connection", (Conn,), {"__module__": "clickhouse_connect.dbapi.connection"})
    conn: Conn = cls()
    _hammer(DBAPIConnector(conn, "clickhouse"))
    assert conn.peak == 1


# -- review round 1 ---------------------------------------------------------------------------


class CancellingCursor(Cursor):
    """A cursor with its own cancel hook: a timeout cancels only its own statement."""

    def cancel(self) -> None:
        pass


class ConnCancelOnly(Conn):
    """A level-2 driver whose only cancel hook is on the connection (psycopg-like)."""

    def cancel(self) -> None:
        pass


def test_level_2_with_connection_only_cancel_is_serialised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # one probe's timer would cancel the connection, killing whatever else runs on it
    mod = types.ModuleType("fake_conn_cancel")
    mod.threadsafety = 2  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_conn_cancel", mod)
    cls = type("Connection", (ConnCancelOnly,), {"__module__": "fake_conn_cancel.connections"})
    conn: Conn = cls()
    _hammer(DBAPIConnector(conn, "postgres"))
    assert conn.peak == 1


def test_duckdb_is_exempt_its_cursor_is_an_independent_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mod = types.ModuleType("duckdb")
    mod.threadsafety = 1  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "duckdb", mod)

    class DuckConn(Conn):
        def cursor(self) -> Cursor:
            return CancellingCursor(self)

    cls = type("DuckDBPyConnection", (DuckConn,), {"__module__": "duckdb"})
    conn: Conn = cls()
    _hammer(DBAPIConnector(conn, "duckdb"))
    assert conn.peak > 1


def test_one_lock_per_connection_across_connectors(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _driver(monkeypatch, "fake_shared", 1)
    a, b = DBAPIConnector(conn, "duckdb"), DBAPIConnector(conn, "duckdb")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda i: (a if i % 2 else b).execute(PROBE, timeout_s=None), range(8)))
    assert conn.peak == 1


def test_waiting_for_the_connection_is_bounded_by_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from factlang.connectors import ExecutionError

    conn = _driver(monkeypatch, "fake_wait", 1)
    connector = DBAPIConnector(conn, "duckdb")
    release = threading.Event()

    def slow(self: Cursor, sql: str, params: Any) -> None:
        release.wait(5)
        self.description = (("row_count",),)

    monkeypatch.setattr(Cursor, "execute", slow)
    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(connector.execute, PROBE, timeout_s=None)
        time.sleep(0.05)
        started = time.monotonic()
        with pytest.raises(ExecutionError) as info:
            connector.execute(PROBE, timeout_s=0.1)
        assert info.value.code == "FL702"
        assert time.monotonic() - started < 2
        release.set()
        holder.result()
