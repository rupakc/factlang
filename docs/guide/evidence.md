# Evidence

A verdict you cannot audit is just another claim. Every measured outcome carries an
`Evidence` record:

| field | |
|---|---|
| `sql` | the query, **display form**: literals inlined and escaped, ready to paste into a console |
| `params` | the values that were bound as typed parameters when the query actually ran |
| `dialect` | the dialect it was rendered for |
| `tier` | how much that rendering is trusted: `verified`, `native` or `best_effort` |
| `duration_ms` | wall-clock time of the query |
| `bytes_processed` | when the connector reports it (BigQuery does) |
| `fused_with` | semantic hashes of the other claims answered by the same query |
| `method` | `exact` for every claim; `sampled:<method>:<fraction>:<seed>` only for [raw sampled measurements](measure.md) |

## What runs is not what you read

The executed statement is a template with neutral placeholders plus an ordered tuple of
typed parameters. `fl.compile` shows both:

```python
import factlang as fl

(probe,) = fl.compile("COUNT orders VALUE ==1400 WHERE status = 'paid'", dialect="postgres")
print(probe.sql_template)
# SELECT COUNT(*) AS row_count FROM orders WHERE status = CAST({{p1}} AS TEXT)
print(probe.params)
# (Param(name='p1', value='paid', family='string'),)
print(probe.bind("pyformat"))
# ('SELECT … WHERE status = CAST(%(p1)s AS TEXT)', {'p1': 'paid'})
print(probe.inline())
# SELECT COUNT(*) AS row_count FROM orders WHERE status = CAST('paid' AS TEXT)
```

Parameters carry their type family, and some profiles (Postgres here) cast each placeholder
to that type explicitly. `bind(paramstyle)` produces the driver's spelling for `qmark`,
`numeric`, `named`, `format` or `pyformat`; each connector declares its own. `inline()` is for people only and is never
executed.

Two guards hold on every rendered probe: it begins with `SELECT` or `WITH`, and it contains
no `;` outside a literal. Identifiers that cannot be represented safely (a NUL or newline, or
longer than the dialect allows) are refused with `FL502`, never sanitised.

## Tiers

| tier | dialects | what it means |
|---|---|---|
| `verified` | `duckdb`, `postgres`, `mysql`, `clickhouse`, `tsql`, `trino` | the native SQL is executed against a real engine in CI, verb by verb |
| `native` | `bigquery`, `snowflake`, `redshift`, `databricks`, `oracle` | native SQL checked by parsing it with sqlglot in that dialect and by golden tests; an opt-in live suite runs it when credentials are present |
| `best_effort` | any other name sqlglot knows (`presto`, `athena`, `hive`, `teradata`, …) | rendered through the optional sqlglot bridge (`pip install "factlang[sqlglot]"`) |

The tier is reported on every outcome, so a host can, for example, accept a `best_effort`
pass only after a human has looked at the SQL.

## No sampling in claims

A claim's numbers are computed over **all** rows matching its filter. No claim, contract or
analyser ever produces a sampled plan; a contract that tries is a bug, reported as `FL404`.
The only row limit in any claim query is the length of the ranked list of undeclared values
that `VALUES` reports, applied after exact aggregation. The conformance suite asserts that
no `LIMIT`, `TOP`, `FETCH FIRST`, `SAMPLE` or `TABLESAMPLE` appears anywhere else.

Why so strict: `LIMIT` returns rows in storage order, not at random, and a "sampled" probe
once reported a 22.5% rate against an exact 6.8%.

## See also

- [Verdicts](verdicts.md)
- [Batching and fusion](batching.md): what `fused_with` means.
