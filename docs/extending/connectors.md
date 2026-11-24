# Connectors

Only connectors know a network. A connector executes one rendered probe and returns its rows
as plain dicts; everything else (rendering, fusion, judging, timeouts' meaning) is FactLang's.
Every code block on this page runs in the test suite.

## The ones that ship

| connector | for |
|---|---|
| `fl.DBAPIConnector(conn, dialect, paramstyle=None)` | any PEP 249 connection: psycopg, duckdb, snowflake-connector, redshift_connector, databricks-sql, pymysql, oracledb, pyodbc, the `trino` driver, clickhouse-connect's DB-API |
| `fl.SQLAlchemyConnector(engine)` | a SQLAlchemy engine; the dialect is inferred from `engine.dialect.name` |
| `fl.connect(url)` | builds a `SQLAlchemyConnector` from a URL (needs `factlang[sqlalchemy]`) |
| `fl.BigQueryConnector(client, project, dataset=None, maximum_bytes_billed=None)` | BigQuery with query parameters and a dry-run cost check |
| `fl.CallableConnector(fn, dialect, paramstyle="qmark")` | your own `fn(sql, params) -> list[dict]`, for hosts that already run SQL |

`DBAPIConnector` reads the driver's `paramstyle`, opens a fresh cursor per probe, cancels a
statement that exceeds `timeout_s` where the driver allows it, and serialises statements on
one connection unless the driver declares PEP 249 `threadsafety >= 2`.

## Writing a connector

The protocol is two attributes and one method:

```python
import duckdb
import factlang as fl
from factlang.connectors import ExecutionError, QueryResult

class LoggingConnector:
    dialect = "duckdb"
    paramstyle = "qmark"

    def __init__(self, conn):
        self.conn = conn
        self.log = []

    def execute(self, probe, *, timeout_s):
        sql, params = probe.bind(self.paramstyle)   # never splice values yourself
        self.log.append(sql)
        try:
            cursor = self.conn.execute(sql, params)
        except duckdb.Error as exc:
            raise ExecutionError(str(exc), code="FL700") from exc
        names = [d[0] for d in cursor.description]
        return QueryResult(rows=[dict(zip(names, row)) for row in cursor.fetchall()])

conn = duckdb.connect()
conn.execute("CREATE TABLE orders AS SELECT range AS id FROM range(10)")
connector = LoggingConnector(conn)
outcome = fl.check("ROWS orders VALUE ==10", connector=connector, catalog=fl.NullCatalog())
assert outcome.verdict is fl.Verdict.PASS and len(connector.log) == 1
```

Rules:

- Bind with `probe.bind(self.paramstyle)`; return every row as a dict keyed by column label.
- Raise `ExecutionError` with `FL700` (failed), `FL701` (over a cost budget) or `FL702`
  (timeout). Anything else you raise is mapped to `FL700`; it becomes an `error` outcome, and
  never a `no_data` one.
- Optional: `dry_run_bytes(probe) -> int | None` prices a probe without running it; it powers
  `max_bytes` and `Checker.estimate`.
- Optional: `async def aexecute(probe, *, timeout_s)` makes an `AsyncConnector` for
  `Checker.acheck`.

## Hosts that run SQL themselves

```python
connector = fl.CallableConnector(
    lambda sql, params: [dict(zip(("row_count",), conn.execute(sql, params).fetchone()))],
    "duckdb",
)
assert fl.check("ROWS orders VALUE ==10", connector=connector,
                catalog=fl.NullCatalog()).verdict is fl.Verdict.PASS
```

A host function cannot be handed the timeout, so enforcing it is the host's job; a
`TimeoutError` it raises becomes `FL702`.

## Credentials are the boundary

FactLang only ever renders `SELECT` statements, and [metadata SQL](../guide/measure.md#metadata-sql)
passes a keyword guard, but the connection's credentials are what really limit what can
happen. Connect as a role that can only read.

## See also

- [Catalogs](catalogs.md)
- [Batching](../guide/batching.md): concurrency, timeouts and the cost guard.
