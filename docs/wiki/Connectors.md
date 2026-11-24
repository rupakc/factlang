# Connectors

> A connector is the only part of FactLang that talks to a network: it takes SQL plus bound parameters, runs them, and returns rows.
> Adapters cover any Python database driver, SQLAlchemy, BigQuery, and a plain callable for hosts that run SQL themselves.
> The protocol is in `connectors/base.py` (core); each vendor adapter is imported lazily and installed through an extra.

## The protocol

```python
class Connector(Protocol):
    dialect: str                                   # profile name
    def execute(self, sql, params, *, timeout_s) -> QueryResult: ...

class QueryResult:
    rows: list[dict[str, Any]]
    bytes_processed: int | None
```

An optional `AsyncConnector.aexecute` serves async code (`Checker.acheck`). A connector also declares its parameter style so the engine can call `probe.bind(...)` correctly.

## Adapters

| Adapter | Use when |
|---|---|
| `DBAPIConnector(conn, dialect)` | any PEP 249 connection: psycopg, duckdb, snowflake-connector, redshift_connector, databricks-sql, pymysql, oracledb, pyodbc, trino, clickhouse-connect |
| `SQLAlchemyConnector(engine)` | you already have an engine; the dialect is inferred from it |
| `BigQueryConnector(client, *, default_project=None, default_dataset=None, maximum_bytes_billed=None)` | BigQuery; uses query parameters and a dry-run cost check |
| `CallableConnector(fn, dialect)` | your host runs the SQL itself |
| `fl.connect(dsn)` | build a connector from a SQLAlchemy URL |

## Timeouts and errors

Each call takes `timeout_s`. A failed query becomes an `error` outcome with FL700 (execution failed) or FL702 (timeout). A probe over a byte budget is FL701. A warehouse rejection is never reported as `no_data`.

## Cost guard and dry run

Connectors that can price a query without running it (BigQuery) enable the `max_bytes` guard and `Checker.estimate`. Others report `bytes=None, method="unknown"`; FactLang never guesses a price.

## Running metadata SQL

`fl.run_metadata(connector, sql, params=(), timeout_s=None)` runs host-written catalog queries (system views, `INFORMATION_SCHEMA`, `SHOW`, `DESCRIBE`) through any connector, with the same timeout, binding and error mapping. It is not a claim and gives no `Outcome`.

A guard (FL703, raised before anything executes) allows a single statement beginning with `SELECT WITH SHOW DESCRIBE DESC EXPLAIN` and refuses `;`, comments, quoting tricks, `EXPLAIN ANALYZE`, and any write, DDL or control keyword. It is deliberately over-strict and not a sandbox; the connection's own credentials are the real security boundary. On `tsql` only `SELECT` is accepted and it is wrapped in a subquery. Pass values as bound parameters.

## Installing drivers

Extras: `sqlglot sqlalchemy bigquery snowflake redshift databricks postgres duckdb mysql clickhouse mssql oracle trino mcp all`.

## See also

- [[Extending-Connectors]]
- [[Catalogs]]
- [[Dialects]]
- [[Optimizer]]
- [[Diagnostics]]
