"""The ``verified`` engines the verb matrix runs on (spec 8.5, 13, 14.2; Task 20).

Each :class:`Engine` knows how to reach one engine (a DSN in ``FACTLANG_<NAME>_DSN``), how to
spell the shop fixture's DDL and literals in its dialect (:class:`Style`) and how to give the
fixture a clean place to live. ``make_shop`` (``conftest.py``) writes the same rows on every
engine, so ``expectations.py`` holds for all of them: dialect type differences (T-SQL has no
``BOOLEAN`` and spells it ``BIT``, ``TIMESTAMP`` is a row version there and ``DATETIME2`` is
used; ClickHouse needs ``Nullable(...)`` for a column that holds NULL) never change a verdict.

An engine whose DSN is unset, whose driver is not importable or that refuses the connection is
**skipped**, unless it is named in ``FACTLANG_REQUIRE_ENGINES`` (comma separated; CI sets it to
every service container), in which case the test **fails**.

DSNs (all optional locally)::

    FACTLANG_POSTGRES_DSN    postgresql://user:pass@host:port/db     (psycopg conninfo/URL)
    FACTLANG_MYSQL_DSN       mysql://user:pass@host:port/db          (pymysql)
    FACTLANG_CLICKHOUSE_DSN  clickhouse://user:pass@host:http_port/db (clickhouse-connect)
    FACTLANG_TSQL_DSN        an ODBC connection string               (pyodbc, Driver 18)
    FACTLANG_TRINO_DSN       trino://user@host:port/memory/default   (trino)
"""

from __future__ import annotations

import datetime as dt
import os
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest

REQUIRE_ENV = "FACTLANG_REQUIRE_ENGINES"
# Column types of the shop fixture are written as neutral tokens and mapped per dialect.
# A trailing ``!`` marks a NOT NULL column.
_TOKEN = re.compile(r"(INT|BIGINT|STR|DEC|TS|BOOL)(?:\((\d+)(?:,(\d+))?\))?(!?)")


def _dq(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _bq(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


@dataclass(frozen=True)
class Style:
    """How one dialect spells the fixture's DDL and literals."""

    name: str
    types: Mapping[str, str]  # token -> native type; ``{n}``/``{p}``/``{s}`` are filled in
    # a nullable column's type is ``nullable.format(type)`` (ClickHouse ``Nullable(...)``);
    # without one, a nullable column is the bare type and a NOT NULL one says so
    nullable: str | None = None
    quote: Callable[[str], str] = _dq
    timestamp_literal: str = "TIMESTAMP '{}'"
    true_false: tuple[str, str] = ("TRUE", "FALSE")
    create_suffix: str = ""
    drop_sql: str = "DROP TABLE IF EXISTS {}"
    # some drivers (trino) only finish a statement once its result has been read
    drain: bool = False

    def column_type(self, token: str) -> str:
        m = _TOKEN.fullmatch(token)
        if m is None:  # pragma: no cover - fixture typo
            raise ValueError(token)
        kind, a, b, bang = m.groups()
        native = self.types[kind].format(n=a, p=a, s=b)
        if self.nullable is not None:
            return native if bang else self.nullable.format(native)
        return native + " NOT NULL" if bang else native

    def literal(self, value: object) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return self.true_false[0] if value else self.true_false[1]
        if isinstance(value, (int, Decimal)):
            return str(value)
        if isinstance(value, dt.datetime):
            return self.timestamp_literal.format(value.strftime("%Y-%m-%d %H:%M:%S"))
        return "'" + str(value).replace("'", "''") + "'"

    def create(self, table: str, columns: Iterable[tuple[str, str]]) -> str:
        cols = ", ".join(f"{self.quote(c)} {self.column_type(t)}" for c, t in columns)
        return f"CREATE TABLE {self.quote(table)} ({cols}){self.create_suffix}"


_ANSI = {
    "INT": "INTEGER",
    "BIGINT": "BIGINT",
    "STR": "VARCHAR({n})",
    "DEC": "DECIMAL({p},{s})",
    "TS": "TIMESTAMP",
    "BOOL": "BOOLEAN",
}

DUCKDB_STYLE = Style("duckdb", _ANSI)
POSTGRES_STYLE = Style("postgres", _ANSI)
TRINO_STYLE = Style("trino", _ANSI, drain=True)
MYSQL_STYLE = Style(
    "mysql",
    # MySQL TIMESTAMP converts through the session time zone and stops at 2038; DATETIME is the
    # naive timestamp the other engines store
    {**_ANSI, "TS": "DATETIME"},
    quote=_bq,
)
TSQL_STYLE = Style(
    "tsql",
    # T-SQL TIMESTAMP is a row version and there is no BOOLEAN
    {**_ANSI, "TS": "DATETIME2", "BOOL": "BIT"},
    timestamp_literal="'{}'",
    true_false=("1", "0"),
)
CLICKHOUSE_STYLE = Style(
    "clickhouse",
    {
        "INT": "Int32",
        "BIGINT": "Int64",
        "STR": "String",
        "DEC": "Decimal({p},{s})",
        "TS": "DateTime",
        "BOOL": "Bool",
    },
    nullable="Nullable({})",
    timestamp_literal="'{}'",
    create_suffix=" ENGINE = MergeTree ORDER BY tuple()",
)


# -- connections ------------------------------------------------------------------------------


@dataclass
class Live:
    """An engine connection with the shop fixture built in it."""

    name: str
    connection: Any
    style: Style
    now: dt.datetime | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def scalar(self, sql: str) -> Any:
        cur = self.connection.cursor()
        try:
            cur.execute(sql)
            return cur.fetchall()[0][0]
        finally:
            close = getattr(cur, "close", None)
            if close is not None:
                close()


def _url(dsn: str) -> Any:
    return urlsplit(dsn)


def _connect_postgres(dsn: str) -> Any:
    import psycopg

    conn = psycopg.connect(dsn, autocommit=True)
    # a schema of its own on the search_path: unqualified refs then resolve through
    # current_schemas(false), never through a hard-coded "public"
    conn.execute("DROP SCHEMA IF EXISTS fl_shop CASCADE")
    conn.execute("CREATE SCHEMA fl_shop")
    conn.execute("SET search_path TO fl_shop")
    return conn


def _connect_mysql(dsn: str) -> Any:
    import pymysql

    u = _url(dsn)
    db = (u.path or "/").lstrip("/") or "factlang"
    conn = pymysql.connect(
        host=u.hostname or "localhost",
        port=u.port or 3306,
        user=unquote(u.username or "root"),
        password=unquote(u.password or ""),
        autocommit=True,
    )
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {_bq(db)}")
        cur.execute(f"CREATE DATABASE {_bq(db)}")
        cur.execute(f"USE {_bq(db)}")
    return conn


def _connect_clickhouse(dsn: str) -> Any:
    from clickhouse_connect import dbapi

    u = _url(dsn)
    db = (u.path or "/").lstrip("/") or "factlang"
    boot = dbapi.connect(
        host=u.hostname or "localhost",
        port=u.port or 8123,
        username=unquote(u.username or "default"),
        password=unquote(u.password or ""),
    )
    cur = boot.cursor()
    cur.execute(f"DROP DATABASE IF EXISTS {_dq(db)}")
    cur.execute(f"CREATE DATABASE {_dq(db)}")
    boot.close()
    return dbapi.connect(
        host=u.hostname or "localhost",
        port=u.port or 8123,
        username=unquote(u.username or "default"),
        password=unquote(u.password or ""),
        database=db,
    )


def _connect_tsql(dsn: str) -> Any:
    import pyodbc

    conn = pyodbc.connect(dsn, autocommit=True)
    cur = conn.cursor()
    cur.execute("IF DB_ID('fl_shop') IS NOT NULL DROP DATABASE fl_shop")
    cur.execute("CREATE DATABASE fl_shop")
    cur.execute("USE fl_shop")
    cur.close()
    return conn


def _connect_trino(dsn: str) -> Any:
    import trino

    u = _url(dsn)
    parts = [p for p in (u.path or "").split("/") if p]
    catalog = parts[0] if parts else "memory"
    schema = parts[1] if len(parts) > 1 else "default"
    return trino.dbapi.connect(
        host=u.hostname or "localhost",
        port=u.port or 8080,
        user=unquote(u.username or "factlang"),
        catalog=catalog,
        schema=schema,
    )


@dataclass(frozen=True)
class Engine:
    name: str
    style: Style
    connect: Callable[[str], Any]

    @property
    def env(self) -> str:
        return f"FACTLANG_{self.name.upper()}_DSN"


ENGINES: tuple[Engine, ...] = (
    Engine("postgres", POSTGRES_STYLE, _connect_postgres),
    Engine("mysql", MYSQL_STYLE, _connect_mysql),
    Engine("clickhouse", CLICKHOUSE_STYLE, _connect_clickhouse),
    Engine("tsql", TSQL_STYLE, _connect_tsql),
    Engine("trino", TRINO_STYLE, _connect_trino),
)
ENGINE_NAMES = ("duckdb", *(e.name for e in ENGINES))


def required_engines() -> frozenset[str]:
    raw = os.environ.get(REQUIRE_ENV, "")
    names = frozenset(n.strip().lower() for n in raw.split(",") if n.strip())
    unknown = names - set(ENGINE_NAMES)
    if unknown:
        raise ValueError(f"{REQUIRE_ENV} names unknown engine(s): {sorted(unknown)}")
    return names


def open_engine(name: str) -> tuple[Any, Style]:
    """A clean connection to ``name``; skips (or fails, when required) if unavailable."""
    required = name in required_engines()

    def unavailable(why: str) -> Any:
        if required:
            pytest.fail(f"engine {name} is required ({REQUIRE_ENV}) but {why}", pytrace=False)
        pytest.skip(f"engine {name}: {why}")

    if name == "duckdb":
        try:
            import duckdb
        except ImportError as exc:
            return unavailable(f"duckdb is not importable ({exc})")
        return duckdb.connect(), DUCKDB_STYLE
    engine = next(e for e in ENGINES if e.name == name)
    dsn = os.environ.get(engine.env)
    if not dsn:
        return unavailable(f"{engine.env} is not set")
    try:
        return engine.connect(dsn), engine.style
    except ImportError as exc:  # also a driver whose native library is missing (pyodbc)
        return unavailable(f"driver not importable ({exc})")
    except Exception as exc:
        return unavailable(f"cannot connect ({type(exc).__name__}: {exc})")


__all__ = [
    "CLICKHOUSE_STYLE",
    "DUCKDB_STYLE",
    "ENGINES",
    "ENGINE_NAMES",
    "MYSQL_STYLE",
    "POSTGRES_STYLE",
    "REQUIRE_ENV",
    "TRINO_STYLE",
    "TSQL_STYLE",
    "Engine",
    "Live",
    "Style",
    "open_engine",
    "required_engines",
]
