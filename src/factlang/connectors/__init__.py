"""Connectors: execute rendered probes on a warehouse (spec 9.1, 9.2; layer 14).

``base``, ``callable``, ``dbapi`` and ``catalog`` are core (stdlib only); ``sqlalchemy`` and
``bigquery`` import their libraries lazily, inside the functions that need them.
"""

from __future__ import annotations

from factlang.connectors.base import (
    EXECUTION_CODES,
    METADATA_KEYWORDS,
    AsyncConnector,
    Connector,
    ExecutionError,
    QueryResult,
    aexecute,
    check_metadata_sql,
    dry_run_bytes,
    run_metadata,
)
from factlang.connectors.bigquery import BigQueryConnector
from factlang.connectors.callable import CallableConnector
from factlang.connectors.catalog import WarehouseCatalog
from factlang.connectors.dbapi import DBAPIConnector
from factlang.connectors.sqlalchemy import SQLAlchemyConnector, connect

__all__ = [
    "EXECUTION_CODES",
    "METADATA_KEYWORDS",
    "AsyncConnector",
    "BigQueryConnector",
    "CallableConnector",
    "Connector",
    "DBAPIConnector",
    "ExecutionError",
    "QueryResult",
    "SQLAlchemyConnector",
    "WarehouseCatalog",
    "aexecute",
    "check_metadata_sql",
    "connect",
    "dry_run_bytes",
    "run_metadata",
]
