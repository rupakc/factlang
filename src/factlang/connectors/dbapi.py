"""DBAPIConnector: any PEP 249 connection (spec 9.1, 18 R7; layer 14, core).

Covers psycopg, duckdb, sqlite3, snowflake-connector, redshift_connector, databricks-sql,
pymysql, oracledb, pyodbc, clickhouse-connect's DB-API and ``trino.dbapi``.
"""

from __future__ import annotations

import contextlib
import sys
import threading
import time
import weakref
from collections.abc import Callable
from typing import Any

from factlang.connectors.base import (
    ExecutionError,
    QueryResult,
    _canceller,
    canonical_dialect,
    check_paramstyle,
    deadline,
    map_error,
    normalise_rows,
    rows_from_cursor,
)
from factlang.sql import PARAMSTYLES, RenderedProbe


def _driver_attr(connection: object, attr: str, accept: Callable[[object], bool]) -> object:
    """The first accepted ``attr`` of the driver module defining ``connection``'s class.

    Walks the class's module path upwards (``trino.dbapi`` before ``trino``); a private
    extension module such as ``_duckdb`` is also looked up without its leading underscore.
    Only already-imported modules are consulted.
    """
    module = type(connection).__module__ or ""
    parts = module.split(".")
    candidates: list[str] = []
    for i in range(len(parts), 0, -1):
        name = ".".join(parts[:i])
        candidates.append(name)
        if name.startswith("_"):
            candidates.append(name.lstrip("_"))
    for name in candidates:
        value = getattr(sys.modules.get(name), attr, None)
        if accept(value):
            return value
    return None


def driver_paramstyle(connection: object) -> str | None:
    """The PEP 249 ``paramstyle`` of the driver module that defines ``connection``'s class."""
    style = _driver_attr(
        connection, "paramstyle", lambda v: isinstance(v, str) and v in PARAMSTYLES
    )
    return style if isinstance(style, str) else None


# Drivers whose declared ``threadsafety`` overstates what one connection allows: the
# clickhouse-connect DB-API declares 2 but its HTTP session refuses a second query while one
# runs ("Attempt to execute concurrent queries within the same session").
_ONE_STATEMENT_AT_A_TIME = frozenset({"clickhouse_connect"})


def driver_threadsafety(connection: object) -> int | None:
    """The PEP 249 ``threadsafety`` level of ``connection``'s driver module, if it declares one.

    A driver known to overstate it (clickhouse-connect) is reported as level 1.
    """
    if (type(connection).__module__ or "").split(".")[0] in _ONE_STATEMENT_AT_A_TIME:
        return 1
    level = _driver_attr(
        connection, "threadsafety", lambda v: isinstance(v, int) and not isinstance(v, bool)
    )
    return level if isinstance(level, int) else None


# Drivers whose ``cursor()`` returns an independent duplicate connection, so threads never
# share a statement stream whatever ``threadsafety`` says (duckdb declares 1).
_INDEPENDENT_CURSORS = frozenset({"duckdb", "_duckdb"})

# One lock per underlying connection object, shared by every DBAPIConnector wrapping it.
_LOCKS: weakref.WeakKeyDictionary[Any, threading.Lock] = weakref.WeakKeyDictionary()
# Fallback for connections that cannot be weakly referenced: id -> (connection, lock); the
# connection is kept alive so its id cannot be reused by another object.
_LOCKS_BY_ID: dict[int, tuple[Any, threading.Lock]] = {}
_LOCKS_GUARD = threading.Lock()


def connection_lock(connection: Any) -> threading.Lock:
    """The lock serialising statements on ``connection`` (one per connection object)."""
    with _LOCKS_GUARD:
        try:
            lock = _LOCKS.get(connection)
            if lock is None:
                lock = _LOCKS[connection] = threading.Lock()
            return lock
        except TypeError:  # not weak-referenceable (or unhashable)
            entry = _LOCKS_BY_ID.get(id(connection))
            if entry is None:
                entry = _LOCKS_BY_ID[id(connection)] = (connection, threading.Lock())
            return entry[1]


class DBAPIConnector:
    """Executes probes on a PEP 249 connection.

    ``paramstyle`` defaults to the driver module's ``paramstyle`` (else ``qmark``). Each probe
    runs on a fresh cursor, closed afterwards. With ``timeout_s`` the statement is cancelled
    through the cursor's ``interrupt()``/``cancel()`` (duckdb, pyodbc), else the connection's
    (sqlite3, psycopg), when the driver has one, and reported as ``FL702``.

    ``Checker`` runs probes from several threads. Statements on one connection run
    concurrently only when that is safe: duckdb (``cursor()`` is an independent duplicate
    connection), or a driver declaring PEP 249 ``threadsafety`` >= 2 whose cursors have their
    own cancel hook (trino). Otherwise they are serialised by one lock per connection object,
    shared by every connector wrapping it: a level-1 driver (pymysql, pyodbc; clickhouse-connect,
    which overstates its level) would interleave two statements, and with only a
    connection-level cancel (psycopg) one probe's timeout would cancel another's statement.
    Waiting for the lock counts against ``timeout_s``; running out while waiting is ``FL702``.
    """

    def __init__(self, connection: Any, dialect: str, paramstyle: str | None = None) -> None:
        self.connection = connection
        self.dialect = canonical_dialect(dialect)
        style = paramstyle if paramstyle is not None else driver_paramstyle(connection)
        self.paramstyle = check_paramstyle(style if style is not None else "qmark")
        if (type(connection).__module__ or "").split(".")[0] in _INDEPENDENT_CURSORS:
            self._may_share = True
        else:
            level = driver_threadsafety(connection)
            self._may_share = level is not None and level >= 2
        # decided on the first cursor (does it carry its own cancel hook?); None = not yet
        self._concurrent: bool | None = None if self._may_share else False

    def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
        if self._concurrent:
            return self._execute(self.connection.cursor(), probe, timeout_s)
        lock = connection_lock(self.connection)
        started = time.monotonic()
        if not lock.acquire(timeout=-1 if timeout_s is None else max(timeout_s, 0.0)):
            raise ExecutionError(
                f"query timed out after {timeout_s:g}s (waiting for the connection)",
                code="FL702",
            )
        try:
            remaining = (
                None if timeout_s is None else max(timeout_s - (time.monotonic() - started), 0.0)
            )
            cursor = self.connection.cursor()
            if self._concurrent is None:
                self._concurrent = _canceller((cursor,)) is not None
            return self._execute(cursor, probe, remaining)
        finally:
            lock.release()

    def _execute(self, cursor: Any, probe: RenderedProbe, timeout_s: float | None) -> QueryResult:
        try:
            sql, params = probe.bind(self.paramstyle)
            bound: Any = params if isinstance(params, dict) else list(params)
            with deadline(timeout_s, (cursor, self.connection)) as fired:
                try:
                    cursor.execute(sql, bound)
                    records = cursor.fetchall() if cursor.description is not None else []
                    rows = rows_from_cursor(cursor.description, records)
                except ExecutionError:
                    raise
                except Exception as exc:
                    raise map_error(exc, timed_out=fired.is_set(), timeout_s=timeout_s) from exc
        finally:
            close = getattr(cursor, "close", None)
            if close is not None:
                with contextlib.suppress(Exception):  # must not mask the result
                    close()
        return QueryResult(normalise_rows(probe, rows))


__all__ = ["DBAPIConnector", "connection_lock", "driver_paramstyle", "driver_threadsafety"]
