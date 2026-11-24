# Raw measurement and sampling

Claims answer "is this true?". Libraries built on FactLang, such as schema profilers, often
need the plain numbers instead: the top values of a column, how two keys overlap, what a scan
would cost. This page covers the three tools for that: **raw measurement**, **cost
estimates** and **metadata SQL**, and the one place sampling is allowed.

Every code block on this page runs against DuckDB in the test suite.

```python
import duckdb
import factlang as fl
from decimal import Decimal
from factlang.semantics.refs import ColumnRef, Ident, TableRef

conn = duckdb.connect()
conn.execute("""
    CREATE TABLE orders AS
    SELECT i AS id, 1 + i % 400 AS customer_id,
           CASE i % 10 WHEN 0 THEN 'refunded' WHEN 1 THEN 'open' ELSE 'paid' END AS status
    FROM range(1, 100001) AS t(i)
""")
conn.execute("CREATE TABLE customers AS SELECT i AS id FROM range(1, 501) AS t(i)")
connector = fl.DBAPIConnector(conn, "duckdb")

orders = TableRef(None, None, Ident("orders"))
customers = TableRef(None, None, Ident("customers"))
```

## Raw measurement

`fl.measure` (and `Checker.measure` / `ameasure` for batches) runs [IR
primitives](../extending/primitives.md) that have no claim, through the same render, cost
guard and execute path as claims, with fusion and de-duplication across the batch.

```python
top = fl.measure(
    fl.ir.ValueMembership(orders, Ident("status"), declared=(), report_limit=20),
    connector=connector,
)
summary, *ranked = top.payload["rows"]
print(summary["non_null"], [(r["value"], r["freq"]) for r in ranked])
# 100000 [('paid', 80000), ('open', 10000), ('refunded', 10000)]
assert top.error is None
assert [(r["value"], r["freq"]) for r in ranked][0] == ("paid", 80000)
```

The first row is the summary (`kind='summary'`: `non_null`, `declared_rows`,
`undeclared_distinct`); the rest are `kind='undeclared'` rows with `value` and `freq`. With
`declared=()` every value is undeclared, so the ranked rows are the exact top-k
observed values (NULLs excluded, ranked by count, then value; `report_limit` up to 1000).

A `Measurement` has:

| field | |
|---|---|
| `plan` | the plan you passed |
| `payload` | exactly what a contract's `derive` would receive: the result row as a dict of [wire-format aliases](../extending/primitives.md#wire-format) for single-row primitives, `{"rows": [...]}` for row sets, `None` when the query returned no rows |
| `evidence` | the same [`Evidence`](evidence.md) an outcome carries |
| `error` | `None`, or the `Diagnostic` (`FL7xx` for execution, `FL4xx`/`FL5xx` when the plan could not be rendered) |
| `verdict_hint` | `error` on an error, `no_data` when no rows came back, else `None` |

Measuring never raises for an execution failure; check `error`.

```python
overlap = fl.measure(
    fl.ir.KeyOverlap(
        left=(ColumnRef(orders, Ident("customer_id")),),
        right=(ColumnRef(customers, Ident("id")),),
    ),
    connector=connector,
)
print(overlap.payload)
assert overlap.payload["distinct_left"] == 400 and overlap.payload["distinct_right"] == 500
```

## Cost estimates

`Checker.estimate` renders claims (text) or plans and asks the connector's optional dry run
for the bytes, **without executing anything**:

```python
checker = fl.Checker(connector)
(estimate,) = checker.estimate("JOIN orders.customer_id -> customers.id MATCH >=99%")
print(estimate.method, estimate.bytes)    # unknown None: DuckDB has no dry run
assert estimate.bytes is None and estimate.method == "unknown"
```

On BigQuery (`BigQueryConnector`) the method is `dry_run` and `bytes` is the warehouse's own
figure. A connector without a dry run never gets a guessed number.

## Metadata SQL

Some hosts must list schemas and tables themselves. `fl.run_metadata(connector, sql, params)`
runs host-written **catalog metadata** SQL (`INFORMATION_SCHEMA`, system views, `SHOW`,
`DESCRIBE`) through any connector, with the same execution, timeout, parameter binding and
error mapping as probes. It returns a `QueryResult`, never an outcome.

```python
result = fl.run_metadata(
    connector,
    "SELECT table_name FROM information_schema.tables WHERE table_name = {{name}}",
    {"name": "orders"},
)
print(result.rows)                     # [{'table_name': 'orders'}]
assert result.rows == [{"table_name": "orders"}]
```

Values go in `{{name}}` parameter tokens and are bound, never spliced. Before anything runs,
a guard refuses the statement with `FL703` unless:

- it is one statement whose first word is `SELECT`, `WITH`, `SHOW`, `DESCRIBE`, `DESC` or
  `EXPLAIN` (not `EXPLAIN ANALYZE`);
- it contains none of `;` `->>` `--` `/*` `*/` `//` `#` `$` `\`, no Oracle `q'` quoting and no
  non-ASCII character outside parameter tokens (some engines fold Unicode case into keywords);
- no write, DDL or control keyword (`INSERT`, `INTO`, `COPY`, `CALL`, `EXEC`, `MERGE`,
  `GRANT`, `SET`, `USE`, `ATTACH`, `PRAGMA`, …) appears anywhere as a whole word.
  The read-only row-limiting phrases `FETCH FIRST|NEXT <n> ROW|ROWS ONLY` and
  `OFFSET <n> ROW|ROWS` (`<n>` a number or one `{{name}}` token) are allowed, so Oracle 12c+
  and Db2 metadata queries can limit rows; any other `FETCH` is refused.

!!! warning "What the guard is, and is not"
    The guard is **lexer-free and deliberately over-strict**. It scans the raw text without
    understanding quotes, so a forbidden word inside a string literal is refused too; pass
    such values as parameters. Modelling each dialect's lexer was rejected after review
    found bypasses through dollar quotes, MySQL `--`, Snowflake `//` and Oracle `q'`.

    It is a **keyword guard, not a sandbox**. A side-effecting function called from a
    `SELECT` (`nextval`, `NEXT VALUE FOR`, `pg_sleep`, `dblink_exec`,
    `pg_terminate_backend`, …) is not detected. **The connection's credentials are the real
    security boundary**: run metadata SQL as a role that can only read.

    The guard trusts the connector's **dialect label**. On `tsql`, where statements need no
    separator, only `SELECT` is accepted, it runs wrapped as
    `SELECT * FROM (<sql>) AS fl_meta_q`, quote characters (`'` `"` `[` `]`) are refused and
    parentheses must balance, so the wrapper cannot be escaped. A SQL Server connection
    labelled with another dialect name loses that protection.

## Sampling very large tables

Exact measurement of a terabyte table can be too slow or too expensive for a profiler. For
**raw measurement only**, a primitive may carry a `SampleSpec`:

```python
spec = fl.ir.SampleSpec("keyhash", Decimal("0.1"), seed=7, key=(Ident("customer_id"),))
sampled = fl.measure(
    fl.ir.ColumnAggregate(orders, None, (fl.Metric.ROW_COUNT,), sample=spec),
    connector=connector,
)
print(sampled.payload, sampled.evidence.method)
# {'row_count': …, 'sampled_rows': …} sampled:keyhash:0.1:7
assert sampled.evidence.method == "sampled:keyhash:0.1:7"
assert sampled.payload["row_count"] == sampled.payload["sampled_rows"] < 100000
```

!!! danger "Measurement only, never verdicts"
    No claim, contract or analyser ever produces a sampled plan, and no verdict (`pass`,
    `fail`, `no_data`) is ever computed from sampled data. A contract whose plan sets
    `sample` is a bug and gets `FL404`. `LIMIT` is never a sampling method.

### Methods

`keyhash`
:   Keeps a row when a deterministic hash of its key columns, salted with the seed, falls in
    the first `fraction` of a million buckets. The same key always lands in the same bucket,
    so a kept key keeps **all** its rows, the same seed gives the same sample on every run,
    and two tables sampled on their join key with the same fraction and seed keep exactly the
    same key values, which is what makes sampled join measurements meaningful. Allowed on
    every primitive.

`system`
:   The warehouse's block sample (`TABLESAMPLE SYSTEM`, with a repeatable seed where the
    dialect has one). Cheap, because unread blocks are never scanned, but coarse.

`bernoulli`
:   The warehouse's row sample: each row is kept independently with probability `fraction`.

A fraction of 1 renders no sample at all. The payload holds the **raw metrics of the sampled
rows** plus `sampled_rows` (`sampled_left_rows` and `sampled_right_rows` for a `KeyOverlap`,
whose two sides take `sample` and `right_sample`). FactLang does not scale anything up: the
estimate is the host's to make. Evidence records the `sample` and
`method = "sampled:<method>:<fraction>:<seed>"`; exact measurements say `exact`.

### Rules

- **`system` and `bernoulli` need a single read (`FL405`).** A `ValueMembership`, a
  `KeyOverlap`, or an aggregate with a keyed metric (`MAX_PER_KEY`, `DUPLICATE_KEYS`, a
  composite `DISTINCT_KEYS`) reads the sampled table more than once, and a block or row
  sample is not guaranteed to repeat between reads (BigQuery, for one, re-evaluates CTEs).
  Use `keyhash` for those.
- **A method the dialect cannot express is `FL506`**, never a silent exact query or a silent
  `LIMIT`.
- **`keyhash` is consistent within one dialect only.** Each dialect hashes with its own
  function, so the same seed keeps the same keys on every table of one warehouse, which is
  all a join needs, but not across warehouses.

### Support by dialect

| dialect | `keyhash` | `system` | `bernoulli` |
|---|---|---|---|
| `postgres` | yes (`hashtextextended`, PG 11+) | yes | yes |
| `duckdb` | yes | `FL506` | yes |
| `snowflake` | yes | `FL506` | yes |
| `databricks` | yes | `FL506` | yes |
| `oracle` | yes | `FL506` | yes |
| `tsql` | yes | `FL506` | `FL506` |
| `bigquery` | yes | `FL506` | `FL506` |
| `redshift` | yes | `FL506` | `FL506` |
| `mysql` | yes | `FL506` | `FL506` |
| `clickhouse` | yes | `FL506` | `FL506` |
| `trino` | yes | `FL506` | `FL506` |
| sqlglot bridge | `FL506` unless the target is one of the above | where sqlglot has `TABLESAMPLE` | where sqlglot has `TABLESAMPLE` |

Block sampling is refused where repeatability with a seed is not established; DuckDB's
`SYSTEM` sample, for example, samples whole vectors and is not repeatable across threads even
with a seed.

## Stability

`fl.measure`, `Checker.measure`/`ameasure`/`estimate`, `Measurement`, `CostEstimate`,
`fl.run_metadata`, `SampleSpec` and the IR primitives with their fields and result columns are
public API under SemVer; see the [Python API](../reference/api.md#stable-api).

## See also

- [IR primitives](../extending/primitives.md)
- [Filter and join fragments](../extending/fragments.md)
- [Diagnostics](../reference/diagnostics.md): `FL404`, `FL405`, `FL506`, `FL703`
