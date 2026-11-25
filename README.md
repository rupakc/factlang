# FactLang

[![CI](https://github.com/rupakc/factlang/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/rupakc/factlang/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/factlang.svg)](https://pypi.org/project/factlang/)
[![Python versions](https://img.shields.io/pypi/pyversions/factlang.svg)](https://pypi.org/project/factlang/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/rupakc/factlang/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-rupakc.github.io%2Ffactlang-informational.svg)](https://rupakc.github.io/factlang/)

FactLang is a small formal language for verifying factual claims against data warehouses.
You write a claim such as "the `orders` table has at least 1,000 rows" or "every
`order_items.order_id` exists in `orders.id`". FactLang checks it statically, compiles it to
read-only SQL for your dialect, runs it over **all** the data, and returns a verdict
(`pass`, `fail`, `no_data`, `error`, …) with the numbers and the SQL behind it.

Why: numbers in LLM answers, schema registries, data contracts and migration plans are
claims about data, and claims drift. FactLang makes them checkable, line by line, with
evidence a human can audit. The core is pure standard-library Python with no dependencies.

**Status: Alpha (0.1.2).** Python 3.10–3.13. Language version `1.0`.
[Documentation](https://rupakc.github.io/factlang/) ·
[Wiki](https://github.com/rupakc/factlang/wiki) ·
[Changelog](https://github.com/rupakc/factlang/blob/main/CHANGELOG.md)

## Thirty seconds

```bash
pip install "factlang[duckdb]"
```

```python
import duckdb
import factlang as fl

conn = duckdb.connect()
conn.execute("""
    CREATE TABLE orders AS
    SELECT i AS id,
           CASE WHEN i % 10 = 0 THEN 'refunded' ELSE 'paid' END AS status
    FROM range(1, 1001) AS t(i)
""")

claims = """
ROWS orders VALUE >=1000
UNIQUE orders.id
VALUES orders.status IN ['paid', 'open']
"""
report = fl.Checker(fl.DBAPIConnector(conn, "duckdb")).check(claims)

for o in report.outcomes:
    print(f"{o.verdict.value:5} {o.claim_text:42} {o.explanation}")
print("verified:", report.verified)

assert [o.verdict.value for o in report.outcomes] == ["pass", "pass", "fail"]
assert not report.verified
```

```text
pass  ROWS orders VALUE >=1000                   row_count 1000 >= 1000
pass  UNIQUE orders.id                           max_rows_per_key 1 <= 1
fail  VALUES orders.status IN ['paid', 'open']   undeclared_rows 100 != 0
verified: False
```

`report.verified` is the flag to gate on: every measurable claim was measured and passed.
Each outcome also carries `facts` (every number the verb measured) and `evidence` (the SQL,
its bind parameters, the dialect and its tier). The
[getting-started guide](https://rupakc.github.io/factlang/getting-started/) goes further.

## Install

```bash
pip install factlang                 # the core: no dependencies at all
pip install "factlang[postgres]"     # plus a driver for your warehouse
```

| extra | adds | for |
|---|---|---|
| `duckdb` | `duckdb` | DuckDB |
| `postgres` | `psycopg[binary]` | PostgreSQL |
| `mysql` | `pymysql` | MySQL |
| `clickhouse` | `clickhouse-connect` | ClickHouse |
| `mssql` | `pyodbc` | SQL Server (`tsql`) |
| `trino` | `trino` | Trino |
| `bigquery` | `google-cloud-bigquery` | BigQuery |
| `snowflake` | `snowflake-connector-python` | Snowflake |
| `redshift` | `redshift-connector` | Redshift |
| `databricks` | `databricks-sql-connector` | Databricks |
| `oracle` | `oracledb` | Oracle |
| `sqlalchemy` | `sqlalchemy` | any SQLAlchemy URL (`--dsn`, `FACTLANG_DSN`) |
| `sqlglot` | `sqlglot` | the long tail of dialects (tier `best_effort`) |
| `mcp` | `mcp` | the MCP server (`factlang mcp`) |
| `all` | every one of the above | |

Any PEP 249 connection works through `fl.DBAPIConnector(conn, "<dialect>")`; the extras only
install drivers.

## The standard verbs

Nineteen verbs, one claim per line. The
[verb reference](https://rupakc.github.io/factlang/reference/verbs/) lists every attribute.

| verb | example | passes when |
|---|---|---|
| `EXISTS` | `EXISTS orders COLUMN shipped_at` | the table (or column) exists in the catalog |
| `TYPE` | `TYPE orders.amount IS decimal` | the column's type family equals the stated one |
| `ROWS` | `ROWS orders VALUE >=1000` | the row count satisfies the comparison |
| `COUNT` | `COUNT orders VALUE ==1400 WHERE status = 'paid'` | the (filtered) row count satisfies the comparison |
| `NULLS` | `NULLS orders.shipped_at RATE <=5%` | the NULL share satisfies the comparison |
| `DEAD` | `DEAD orders.legacy_flag` | every value is NULL |
| `UNIQUE` | `UNIQUE order_items.order_id WITH [line_no]` | no (composite) key is held by more than one row |
| `FANOUT` | `FANOUT order_items.order_id MAX_PER_KEY <=50` | the busiest key's row count satisfies the comparison |
| `VALUES` | `VALUES orders.status IN ['paid', 'open']` | every non-NULL value is declared |
| `RANGE` | `RANGE orders.amount BETWEEN [0, 10000]` | min and max lie inside the closed range |
| `PATTERN` | `PATTERN customers.email LIKE '%@%.%' RATE >=99%` | the share matching the pattern satisfies the comparison |
| `FRESH` | `FRESH events.created_at WITHIN 24h` | the newest value is no older than the duration |
| `JOIN` | `JOIN orders.customer_id -> customers.id CARD N:1 MATCH >=99%` | the share of left keys that find a right key satisfies the comparison |
| `DISTINCT` | `DISTINCT orders.customer_id VALUE ==512` | the distinct non-NULL count satisfies the comparison |
| `SUM` | `SUM orders.amount VALUE ==209000 TOLERANCE 1%` | the sum satisfies the comparison |
| `AVG` | `AVG orders.amount VALUE ==104.5 ABS_TOLERANCE 0.01` | the average satisfies the comparison |
| `MIN` | `MIN orders.amount VALUE >=0` | the smallest value satisfies the comparison |
| `MAX` | `MAX orders.placed_on VALUE <=DATE '2026-12-31'` | the largest value satisfies the comparison |
| `NOTE` | `NOTE orders.amount IS eur BECAUSE 'all prices are stored in euro'` | never measured: a recorded semantic fact |

Every verb accepts a `WHERE` filter where it makes sense. You can add, override, alias or
remove verbs in your own [pack](https://rupakc.github.io/factlang/extending/packs/).

## Dialects

Eleven dialects are rendered natively by the pure-Python core; every outcome reports the
**tier** of the SQL that produced it.

| tier | dialects | what it means |
|---|---|---|
| `verified` | `duckdb`, `postgres`, `mysql`, `clickhouse`, `tsql`, `trino` | the native SQL runs against a real engine in CI, verb by verb |
| `native` | `bigquery`, `snowflake`, `redshift`, `databricks`, `oracle` | native SQL checked by parsing it with sqlglot in that dialect and by golden tests; an opt-in live suite runs it when credentials are present |
| `best_effort` | any other dialect sqlglot can write (`presto`, `athena`, `hive`, `teradata`, …) | rendered through the optional sqlglot bridge (`pip install "factlang[sqlglot]"`) |

## Command line

```bash
factlang lint facts.fl                       # diagnostics only; never executes
factlang fmt --check facts.fl                # canonical formatting, for CI
factlang explain --dialect bigquery facts.fl # print each claim's SQL
export FACTLANG_DSN="postgresql+psycopg://reader@warehouse/shop"
factlang check facts.fl                      # run the claims (needs factlang[sqlalchemy])
factlang check --format json facts.fl        # also: --format markdown
factlang verbs                               # list the standard verbs
```

| exit code | meaning |
|---|---|
| `0` | every claim passed (or is unprobeable) |
| `1` | a claim failed; for `fmt --check`, a file would be reformatted |
| `2` | invalid input: bad usage, an unreadable file, a claim with front-end errors, a connector that cannot be loaded |
| `3` | errors or undecidable: an `error` or `no_data` outcome, a claim that cannot be rendered |

When several apply, `2` wins over `1`, and `1` over `3`. See the
[CLI guide](https://rupakc.github.io/factlang/cli/).

## Agents and MCP

FactLang ships tool definitions for LLM agents (`factlang_check`, `factlang_lint`,
`factlang_explain`) and serves the same tools over MCP on stdio:

```bash
pip install "factlang[mcp,sqlalchemy]"
export FACTLANG_DSN="postgresql+psycopg://reader@warehouse/shop"
factlang mcp                          # register this as a stdio command in your MCP client
factlang mcp --dialect snowflake      # no warehouse: lint and explain only
```

Have the model emit claims alongside its answer and gate on `verified`. See
[Agents and LLMs](https://rupakc.github.io/factlang/agents/).

## Guarantees

- **No sampling in claims.** Every verdict is computed over every row that matches the
  claim's filter. Sampling exists only in `Checker.measure`, the raw-measurement API, when
  you ask for it, and its evidence says so
  ([details](https://rupakc.github.io/factlang/guide/measure/)).
- **Read-only SQL, typed parameters.** Probes are `SELECT`-only, claims cannot contain raw
  SQL, and every literal travels as a typed bind parameter. Use a database role that can
  only read: **your credentials are the security boundary.**
- **Undecidable stays undecidable.** A fact the data cannot support is `no_data`, never a
  pass; a query the warehouse rejects is `error`.
- **Stable API.** The names in `fl.__all__` and the
  [stable API table](https://rupakc.github.io/factlang/reference/api/) follow SemVer: no
  breaking change within a `0.x` minor series.

## Project

- [Documentation](https://rupakc.github.io/factlang/) and the in-browser
  [playground](https://rupakc.github.io/factlang/playground/)
- [Wiki](https://github.com/rupakc/factlang/wiki): concepts, architecture, design decisions
- [Changelog](https://github.com/rupakc/factlang/blob/main/CHANGELOG.md)
- [Contributing](https://github.com/rupakc/factlang/blob/main/CONTRIBUTING.md)
- [Security policy](https://github.com/rupakc/factlang/blob/main/SECURITY.md)
- [License: MIT](https://github.com/rupakc/factlang/blob/main/LICENSE)
