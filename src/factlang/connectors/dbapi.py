"""DBAPIConnector: any PEP 249 connection (spec 9.1, 18 R7; layer 14, core).

Covers psycopg, duckdb, sqlite3, snowflake-connector, redshift_connector, databricks-sql,
pymysql, oracledb, pyodbc, clickhouse-connect's DB-API and ``trino.dbapi``.
"""

from __future__ import annotations

import contextlib
import sys
import threading
from collections.abc import Callable
from typing import Any

from factlang.connectors.base import (
    ExecutionError,
    QueryResult,
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


class DBAPIConnector:
    """Executes probes on a PEP 249 connection.

    ``paramstyle`` defaults to the driver module's ``paramstyle`` (else ``qmark``). Each probe
    runs on a fresh cursor, closed afterwards. With ``timeout_s`` the statement is cancelled
    through the cursor's ``interrupt()``/``cancel()`` (duckdb, pyodbc), else the connection's
    (sqlite3, psycopg), when the driver has one, and reported as ``FL702``.

    ``Checker`` runs probes from several threads. Unless the driver declares PEP 249
    ``threadsafety`` >= 2 (threads may share a connection: psycopg, trino),
    statements on the connection are serialised by a lock: a level-1 driver (pymysql, pyodbc,
    duckdb; clickhouse-connect, which overstates its level) would otherwise interleave two
    statements on one connection.
    """

    def __init__(self, connection: Any, dialect: str, paramstyle: str | None = None) -> None:
        self.connection = connection
        self.dialect = canonical_dialect(dialect)
        style = paramstyle if paramstyle is not None else driver_paramstyle(connection)
        self.paramstyle = check_paramstyle(style if style is not None else "qmark")
        level = driver_threadsafety(connection)
        self._lock: threading.Lock | None = (
            None if level is not None and level >= 2 else threading.Lock()
        )

    def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
        if self._lock is None:
            return self._execute(probe, timeout_s)
        with self._lock:
            return self._execute(probe, timeout_s)

    def _execute(self, probe: RenderedProbe, timeout_s: float | None) -> QueryResult:
        sql, params = probe.bind(self.paramstyle)
        bound: Any = params if isinstance(params, dict) else list(params)
        cursor = self.connection.cursor()
        try:
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


__all__ = ["DBAPIConnector", "driver_paramstyle", "driver_threadsafety"]
