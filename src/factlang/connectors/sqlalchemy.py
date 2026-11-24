"""SQLAlchemyConnector and ``connect(dsn)`` (spec 9.1; layer 14, optional extra ``sqlalchemy``).

SQLAlchemy is imported lazily, inside the functions that need it.
"""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping
from types import MappingProxyType, ModuleType
from typing import Any

from factlang.connectors.base import (
    ExecutionError,
    QueryResult,
    deadline,
    map_error,
    normalise_rows,
)
from factlang.sql import RenderedProbe
from factlang.sql.probe import bind_template

# SQLAlchemy dialect name -> FactLang profile name (others map to themselves)
DIALECT_NAMES: Mapping[str, str] = MappingProxyType(
    {"postgresql": "postgres", "mssql": "tsql", "mariadb": "mysql"}
)


def _sqlalchemy() -> ModuleType:
    try:
        return importlib.import_module("sqlalchemy")
    except ImportError as exc:
        raise ImportError(
            "SQLAlchemy is required for this connector: pip install factlang[sqlalchemy]"
        ) from exc


# a token right after ':' or a word character, right before a word character, or two
# tokens back to back: text() cannot see ':name' as a bind there
_GLUED_TOKEN = re.compile(r"[:\w]\{\{|\}\}\w|\}\}\{\{")


def escape_colons(template: str) -> str:
    """Escape every ``:`` for ``text()``, which would read ``:word`` as a bind parameter even
    inside a string literal; ``text()`` turns ``\\:`` back into ``:``. Neutral ``{{name}}``
    tokens contain no colon, so the ``:name`` placeholders bound afterwards stay live."""
    return template.replace(":", "\\:")


def _raw_connection(conn: Any) -> object | None:
    """The DB-API connection under a SQLAlchemy ``Connection`` (for cancelling), if any."""
    fairy = getattr(conn, "connection", None)
    raw = getattr(fairy, "dbapi_connection", None)
    return raw if raw is not None else getattr(fairy, "driver_connection", None)


class SQLAlchemyConnector:
    """Executes probes through a SQLAlchemy engine: ``text()`` with ``named`` binding."""

    paramstyle = "named"

    def __init__(self, engine: Any) -> None:
        self.engine = engine
        name = str(engine.dialect.name)
        self.dialect = DIALECT_NAMES.get(name, name)

    def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
        sa = _sqlalchemy()
        if _GLUED_TOKEN.search(probe.sql_template):
            raise ExecutionError(
                "a {{name}} parameter glued to ':', a word character or another parameter "
                "cannot be bound through SQLAlchemy text(); separate them with a space",
                code="FL700",
            )
        sql, params = bind_template(
            escape_colons(probe.sql_template), probe.params, self.paramstyle
        )
        try:
            with self.engine.connect() as conn:
                raw = _raw_connection(conn)
                with deadline(timeout_s, (raw,) if raw is not None else ()) as fired:
                    try:
                        result = conn.execute(sa.text(sql), params)
                        rows = [dict(row._mapping) for row in result]
                    except ExecutionError:
                        raise
                    except Exception as exc:
                        raise map_error(exc, timed_out=fired.is_set(), timeout_s=timeout_s) from exc
        except ExecutionError:
            raise
        except Exception as exc:
            raise map_error(exc, timeout_s=timeout_s) from exc
        return QueryResult(normalise_rows(probe, rows))


def connect(dsn: str) -> SQLAlchemyConnector:
    """A connector for a SQLAlchemy URL (``postgresql+psycopg://…``, ``duckdb:///…``)."""
    sa = _sqlalchemy()
    return SQLAlchemyConnector(sa.create_engine(dsn))


__all__ = ["DIALECT_NAMES", "SQLAlchemyConnector", "connect", "escape_colons"]
