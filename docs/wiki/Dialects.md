# Dialects

> A dialect profile is a small table of decisions (how to quote a name, how to count, what a table path looks like) that turns the shared SQL tree into one warehouse's SQL.
> Eleven warehouses are covered natively, including Trino, and any other dialect sqlglot knows is available as best effort.
> Every literal in the output is a typed bind parameter; nothing you write in a claim is ever pasted into SQL.

## Native profiles

| Profile | Catalog slot | Schema slot | Max table parts | Tier |
|---|---|---|---|---|
| `trino` | catalog | schema | 3 | verified |
| `duckdb` | database | schema | 3 | verified |
| `postgres` | database | schema | 3 | verified |
| `mysql` (also MariaDB) | none | database | 2 | verified |
| `clickhouse` | none | database | 2 | verified |
| `tsql` (SQL Server, Synapse, Fabric) | database | schema | 3 | verified |
| `bigquery` | project | dataset | 3 | native |
| `snowflake` | database | schema | 3 | native |
| `redshift` | database | schema | 3 | native |
| `databricks` (Spark SQL) | catalog | schema | 3 | native |
| `oracle` | none | schema (owner) | 2 | native |

A table path with more parts than the dialect accepts is refused as FL505. Trino was added in Amendment A (see [[Design-Decisions]]); it needs the `trino` extra to run, since the driver is a separate package.

## What a profile decides

A `DialectProfile` is a frozen dataclass: quoting, path qualification, the way to count conditionally (`COUNTIF`, `SUM(CASE ...)`, `COUNT_IF`), type casts per type family, string concatenation, the "now" expression, limit style, reserved words, case folding of unquoted names, native-type to family mapping, driver parameter types, information-schema query template, maximum identifier length, and for sampling the supported sample styles and the keyhash expression. A profile may also override how one SQL node prints.

## Identifier rules

The governing rule: **a claim identifier means exactly what the same identifier means written in that warehouse's SQL.**

- Unquoted names are emitted unquoted, so the warehouse applies its own case rules (including settings like MySQL's `lower_case_table_names`). If the name is a reserved word it is quoted after applying the dialect's case folding.
- Quoted names are emitted quoted with the dialect's quote character, inner quotes doubled, case preserved.
- A name with a NUL or newline, or longer than the dialect allows, is refused (FL502), never cleaned up.

## Literals are bind parameters

A rendered probe (`RenderedProbe`) holds a neutral SQL template, an ordered tuple of typed parameters, the arity, the plan, the dialect and the tier. The parameter style (`qmark`, `numeric`, `named`, `format`, `pyformat`) is chosen at bind time, because one dialect can have drivers with different styles:

```python
import factlang as fl

(probe,) = fl.compile("COUNT orders VALUE >=1 WHERE status = 'shipped'", dialect="postgres")
sql, params = probe.bind("pyformat")   # what a driver wants
print(probe.inline())                  # display-oriented text; never executed by FactLang
```

Rendered probes must begin with `SELECT` or `WITH` and contain no `;` outside literals.

`inline()` fails closed: it quotes strings by doubling `'` in every dialect and never relies on backslash escaping, and it refuses with `FL507` any value it cannot render safely (a string with a backslash, NUL, line terminator or lone surrogate; a `'` where the resolved dialect's lexer has no doubled-quote form, such as BigQuery, Databricks, Spark or Hive; a subclass of a scalar type; a non-scalar, `NaN`, an infinity or an over-long number; a type that does not match its family). Literal spellings come from the profile (`doubled_quote_escape`, `string_literal_prefix`, `boolean_literals`, `date_literal`, `timestamp_literal`): T-SQL writes `N'…'` and `1`/`0`, Oracle `1`/`0`. Evidence then shows the `{{name}}` template instead; the check itself is unaffected.

## Type families

To answer `TYPE` claims, each profile maps native type names to one of: `integer, decimal, float, string, boolean, date, timestamp, time, bytes, json, array, struct, other`. It first unwraps wrappers such as `Nullable(...)` and maps scale-0 decimals (`NUMBER(38,0)`) to `integer`. A fixture list of each engine's type names tests every table.

## Sampling support

Profiles say which sample methods they can express (`system`, `bernoulli`, `keyhash`). Asking for one they cannot gives FL506, never a silent fallback. See [[Measurement-API]].

## Resolution order

`dialect="x"` means: native profile if there is one; else the sqlglot bridge if installed; else FL504 listing the native names and the hint `pip install factlang[sqlglot]`. `Checker(..., prefer="sqlglot")` forces the bridge, used by tests to cross-check.

## Fragments

`fl.sql.render_filter` and `fl.sql.render_join` render a bare boolean expression or a `FROM ... JOIN ... ON ...` snippet through the same tree and rules. They are never executed by FactLang. See [[Measurement-API]].

## See also

- [[sqlglot-Bridge]]
- [[IR]]
- [[Extending-Dialects]]
- [[Connectors]]
- [[Concepts]]
