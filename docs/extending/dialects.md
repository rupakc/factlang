# Dialects

Only the dialect layer knows SQL. Every IR primitive is first lowered to one internal,
dialect-free SQL tree; two printers then turn that tree into text:

1. **Native profiles**, the verified path. A `DialectProfile` is a frozen dataclass of small
   decisions: how to quote, how unquoted names fold, the reserved words, how to count
   conditionally (`COUNTIF`, `COUNT_IF`, `SUM(CASE …)` or `FILTER`), how to limit, the cast
   type for each type family, the clock expression, the catalog query, how to sample, and
   so on. Eleven ship: `bigquery`, `snowflake`, `redshift`, `postgres`, `duckdb`,
   `databricks`, `clickhouse`, `tsql`, `mysql` (also MariaDB), `oracle`, `trino`.
2. **The sqlglot bridge** (`pip install "factlang[sqlglot]"`) converts the same tree to a
   sqlglot expression and asks sqlglot for any other dialect: `presto`, `athena`, `hive`,
   `teradata`, `starrocks`, `doris`, `exasol`, `dremio`, … Its tier is `best_effort`.

Because both start from the same tree, they cannot disagree on what a claim means. For
`dialect="x"` FactLang uses the native profile if one exists, else the bridge if sqlglot is
installed, else `FL504`. `Checker(..., prefer="sqlglot")` forces the bridge, which is how the
test suite cross-checks native SQL.

Every code block on this page runs in the test suite.

```python
import dataclasses
import factlang as fl

print(fl.sql.native_dialects())
(probe,) = fl.compile("NULLS customers.email RATE <=5%", dialect="tsql")
print(probe.inline())
# SELECT COUNT_BIG(*) AS row_count, COUNT_BIG(email) AS non_null FROM customers
```

## A custom profile

Most engines speak a close cousin of a dialect that already has a profile. Start from that
profile and change what differs:

```python
postgres = fl.sql.get_profile("postgres")
cockroach = dataclasses.replace(
    postgres,
    name="cockroach",
    tier="native",
    sample_styles={},      # declare no table-sample method: FL506 instead of a guess
    keyhash_expr=None,     # no keyhash sampling either
)
fl.sql.register_profile(cockroach)

(probe,) = fl.compile("ROWS orders VALUE >=1", dialect="cockroach")
assert probe.dialect == "cockroach" and probe.tier == "native"
```

`register_profile` raises on an existing name unless `replace=True`. Template fields
(`information_schema_sql`, `sample_styles`, `keyhash_expr`, …) are validated when the profile
is built. The rare node a dialect must spell differently goes in `overrides`, a mapping from
an internal-AST node type to a print function.

A new profile needs the conformance suite: every primitive and every metric rendered, the
metric aliases unchanged (they are a [wire format](primitives.md#wire-format)), and the type
family table tested against that engine's type names. The repository's `add-dialect` skill
walks through it.

## Dialect notes worth knowing

- **Identifiers** mean what they would mean in that warehouse's SQL; see
  [Namespaces](../guide/namespaces.md#case).
- **Table parts**: MySQL, ClickHouse and Oracle accept two parts, the rest three (`FL505`).
- **SQL Server** (`tsql`): `COUNT_BIG` everywhere, `TOP` for limits, native `SUM` (an
  overflow is an `error`, never a wrong number), `AVG(x + 0.0)` (one fractional digit lost on
  `DECIMAL(38, s>0)`). See [Standard verbs](../guide/verbs.md#answer-facts).
- **Sampling** support differs by dialect; see the
  [table](../guide/measure.md#support-by-dialect).
- **Casts**: a `JOIN … CAST <family>` to a family a dialect cannot express is `FL503`.

## See also

- [Evidence: tiers](../guide/evidence.md#tiers)
- [IR primitives](primitives.md)
