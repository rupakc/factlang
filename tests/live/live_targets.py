"""Live warehouse targets for ``tests/live`` (see ``conftest.py``)."""

from __future__ import annotations

import importlib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

LIVE_DIALECTS = ("bigquery", "snowflake", "redshift", "databricks", "oracle")


@dataclass(frozen=True)
class LiveTarget:
    dialect: str
    connector: Any
    table: str
    column: str


def _dbapi(module: str, attr: str = "connect") -> Callable[[dict[str, Any], str], Any]:
    def build(kwargs: dict[str, Any], dialect: str) -> Any:
        from factlang.connectors import DBAPIConnector

        connect = getattr(importlib.import_module(module), attr)
        return DBAPIConnector(connect(**kwargs), dialect)

    return build


def _bigquery(kwargs: dict[str, Any], dialect: str) -> Any:
    from google.cloud import bigquery

    from factlang.connectors import BigQueryConnector

    dataset = kwargs.pop("default_dataset", None)
    client = bigquery.Client(**kwargs)
    return BigQueryConnector(client, default_project=client.project, default_dataset=dataset)


_BUILDERS: dict[str, Callable[[dict[str, Any], str], Any]] = {
    "bigquery": _bigquery,
    "snowflake": _dbapi("snowflake.connector"),
    "redshift": _dbapi("redshift_connector"),
    "databricks": _dbapi("databricks.sql"),
    "oracle": _dbapi("oracledb"),
}


def _env(dialect: str, suffix: str = "") -> str:
    return f"FACTLANG_LIVE_{dialect.upper()}{suffix}"


def open_target(dialect: str) -> LiveTarget:
    """The connector for ``dialect``: skipped unless enabled, failed when misconfigured."""
    if os.environ.get(_env(dialect)) != "1":
        pytest.skip(f"{_env(dialect)}=1 is not set")
    missing = [s for s in ("_TABLE", "_COLUMN") if not os.environ.get(_env(dialect, s))]
    if missing:
        pytest.fail(f"{_env(dialect)}=1 needs {', '.join(_env(dialect, s) for s in missing)}")
    kwargs = json.loads(os.environ.get(_env(dialect, "_CONNECT"), "{}"))
    connector = _BUILDERS[dialect](kwargs, dialect)
    return LiveTarget(
        dialect,
        connector,
        os.environ[_env(dialect, "_TABLE")],
        os.environ[_env(dialect, "_COLUMN")],
    )


__all__ = ["LIVE_DIALECTS", "LiveTarget", "open_target"]
