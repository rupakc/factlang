"""DBAPIConnector: any PEP 249 connection (spec 9.1, 18 R7; layer 14, core).

Covers psycopg, duckdb, sqlite3, snowflake-connector, redshift_connector, databricks-sql,
pymysql, oracledb, pyodbc, clickhouse-connect's DB-API and ``trino.dbapi``.
"""

from __future__ import annotations

import contextlib
import sys
from typing import Any

from factlang.connectors.base import (
    ExecutionError,
    QueryResult,
    check_paramstyle,
    deadline,
    map_error,
    rows_from_cursor,
)
from factlang.sql import PARAMSTYLES, RenderedProbe


def driver_paramstyle(connection: object) -> str | None:
    """The PEP 249 ``paramstyle`` of the driver module that defines ``connection``'s class.

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
        style = getattr(sys.modules.get(name), "paramstyle", None)
        if isinstance(style, str) and style in PARAMSTYLES:
            return style
    return None


class DBAPIConnector:
    """Executes probes on a PEP 249 connection.

    ``paramstyle`` defaults to the driver module's ``paramstyle`` (else ``qmark``). Each probe
    runs on a fresh cursor, closed afterwards. With ``timeout_s`` the statement is cancelled
    through the cursor's ``interrupt()``/``cancel()`` (duckdb, pyodbc), else the connection's
    (sqlite3, psycopg), when the driver has one, and reported as ``FL702``.
    """

    def __init__(self, connection: Any, dialect: str, paramstyle: str | None = None) -> None:
        self.connection = connection
        self.dialect = dialect
        style = paramstyle if paramstyle is not None else driver_paramstyle(connection)
        self.paramstyle = check_paramstyle(style if style is not None else "qmark")

    def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
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
        return QueryResult(rows)


__all__ = ["DBAPIConnector", "driver_paramstyle"]
