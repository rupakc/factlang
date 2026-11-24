# Evidence

A verdict you cannot audit is just another claim. Every measured outcome carries an
`Evidence` record:

| field | |
|---|---|
| `sql` | the query, **display form**: literals inlined, ready to paste into a console; the `{{name}}` template when a literal cannot be inlined safely |
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
`numeric`, `named`, `format` or `pyformat`; each connector declares its own.

Two guards hold on every rendered probe: it begins with `SELECT` or `WITH`, and it contains
no `;` outside a literal. Identifiers that cannot be represented safely (a NUL or newline, or
longer than the dialect allows) are refused with `FL502`, never sanitised.

## Inlining is display-oriented, and fails closed

`inline()` is for people: FactLang never executes it. Because a host might still run it (a
driver that cannot bind, a console paste), it is built to be safe if executed, and refuses
whatever it cannot render safely instead of guessing:

- Strings are quoted by doubling `'`, in every dialect. Nothing relies on backslash escaping,
  so a literal never changes meaning with server settings (MySQL's default `sql_mode`,
  PostgreSQL's `standard_conforming_strings`).
- A string holding a backslash, a NUL or a lone surrogate is refused, as is a `'` in a
  dialect whose lexer does not read `''` as a quote. That is decided by the dialect the
  renderer resolves: a native profile's `doubled_quote_escape` (`False` for `bigquery` and
  `databricks`), or, for a bridged dialect, sqlglot's own tokenizer (so `spark`, `spark2` and
  `hive` refuse it too). A dialect nothing knows refuses it.
- Only values of an exact built-in type are inlined: `None`, `bool`, `int`, `float`,
  `Decimal`, `str`, `datetime.date` and `datetime.datetime`. A subclass of any of them (an
  `IntEnum`, a numpy scalar, a `str` subclass) is refused, because its own methods could
  print anything; values are rendered through the base type's functions or from their
  fields. Lists, dicts, sets, bytes, other objects, `NaN`, infinities, integers or decimals
  with more than 4300 digits, and a value whose type does not match its parameter family (a
  `datetime` in a `date` parameter, `True` in an `integer` one) are refused.
- Spellings follow the dialect: T-SQL strings are national literals (`N'…'`); T-SQL and
  Oracle booleans are `1`/`0`; timestamps are converted to UTC and written as
  `CAST('… …' AS DATETIME2)` on T-SQL, `TIMESTAMP '… …'` on Oracle, and
  `TIMESTAMP '…T…+00:00'` elsewhere; T-SQL dates are `CAST('…' AS DATE)`.

A refusal is `RenderError("FL507 cannot inline <type> parameter <name> …")`. Evidence never
fails a check over it: `evidence.sql` (and `factlang explain`, the agent's `factlang_explain`
and `Checker.estimate`) shows the template with its `{{name}}` placeholders instead, and the
values are in `params`; any other exception raised while converting a value is treated the
same way. The claim itself is judged exactly as before, because the executed statement
always binds its parameters. (`FL500` stays a genuine render bug and is never swallowed.)

!!! warning "What inline text assumes"
    Inline text is for display, and for hosts that cannot bind parameters. Bind whenever you
    can. The literals assume the client connection uses UTF-8 (or a single-byte character
    set in which every byte is a whole character): a multi-byte client charset such as SJIS,
    Big5 or GBK talking to a server that also honours backslash escapes is out of scope.
    Console tools may reinterpret pasted text before the server sees it: SQL*Plus
    substitutes `&name`, SSMS and `sqlcmd` split batches at a line holding `GO`, and drivers
    that parse paramstyle markers can read a `%s`, `%(name)s` or `?` inside a literal as a
    placeholder. Pass inline text to such a tool only after checking it.

```python
import factlang as fl

from factlang.engine import display_sql

(probe,) = fl.compile(r"COUNT orders VALUE ==0 WHERE status = 'a\b'", dialect="mysql")
try:
    probe.inline()
except fl.sql.RenderError as exc:
    print(exc)  # FL507 cannot inline str parameter p1: it contains a backslash or NUL, ...
print(display_sql(probe))
# SELECT COUNT(*) AS row_count FROM orders WHERE status = {{p1}}
assert display_sql(probe) == probe.sql_template
```

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
