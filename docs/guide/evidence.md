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
  so a string literal does not depend on string-escape settings (MySQL's default `sql_mode`,
  PostgreSQL's `standard_conforming_strings`).
- A string holding a backslash, a NUL, a lone surrogate or a line terminator (CR, LF, U+0085,
  U+2028, U+2029) is refused, as is a `'` in a dialect whose lexer does not read `''` as a
  quote. That is decided by the dialect the renderer resolves: a native profile's
  `doubled_quote_escape` (`False` for `bigquery` and `databricks`), or, for a bridged
  dialect, sqlglot's own tokenizer (so `spark`, `spark2` and `hive` refuse it too). A
  dialect nothing knows refuses it. A custom profile defaults to `False`, and when its name
  is also a dialect sqlglot knows, doubling is used only if that dialect's lexer reads `''`
  as a quote too (a postgres-like profile registered as `hive` still refuses `'`).
- Only values of an exact built-in type are inlined: `None`, `bool`, `int`, `float`,
  `Decimal`, `str`, `datetime.date` and `datetime.datetime`. A subclass of any of them (an
  `IntEnum`, a numpy scalar, a `str` subclass) is refused, because its own methods could
  print anything; values are rendered through the base type's functions or from their
  fields. Lists, dicts, sets, bytes, other objects, `NaN`, infinities and a value whose type
  does not match its parameter family (a `datetime` in a `date` parameter, `True` in an
  `integer` one) are refused.
- Numbers are inlined only where every engine reads the literal as that exact value: an
  `int` within the signed 64-bit range, and a `Decimal` whose plain form needs a precision
  (integer digits plus scale, what `DECIMAL(p,s)` must hold) of at most 38, so
  `Decimal('1E+50')` (precision 51) is refused. Past those limits engines silently change
  the literal's type or precision (DuckDB reads a 39-digit fraction as `DOUBLE`, a 20-digit integer as `HUGEINT`). A
  finite `float` always inlines: its shortest `repr` never has more than 17 significant
  digits. Bound parameters have no such limits.
- Spellings follow the dialect. T-SQL strings are national literals (`N'…'`); T-SQL, Oracle
  and Teradata booleans are `1`/`0`. Timestamps are converted to UTC; fractional seconds
  (`.ffffff`) are written only when the microseconds are not zero. Dates are `DATE '…'`
  everywhere except T-SQL (`CAST('…' AS DATE)`).

    Timestamp literals:

    - `trino`, and bridged `presto`, `athena` and Trino-derived dialects:
      `TIMESTAMP 'YYYY-MM-DD HH:MM:SS[.ffffff] UTC'`;
    - `databricks`, `oracle`, and bridged `hive`, `spark`, `spark2`, `teradata`, `exasol`:
      `TIMESTAMP 'YYYY-MM-DD HH:MM:SS[.ffffff]'`;
    - `tsql` (and bridged `fabric`): `CAST('YYYY-MM-DD HH:MM:SS[.ffffff]' AS DATETIME2)`;
    - every other dialect: `TIMESTAMP 'YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00'`.

    A bridged dialect takes its spellings from a small table of sqlglot dialect names
    (`BRIDGE_LITERAL_STYLES` in `factlang.sql.sqlglot_bridge`), else from the nearest native
    profile it derives from in sqlglot (Fabric from T-SQL, Materialize from Postgres), else
    the ANSI forms. Whether `''` may be used is always decided by the dialect's own lexer.
- On Trino-family dialects the literal carries an explicit `UTC` zone, so it is the UTC
  instant whatever the session time zone. Elsewhere a timestamp inlines as a literal most
  engines read as a naive (time-zone-less) value holding UTC wall-clock time; compared with
  a time-zone-aware column (`timestamptz`, `DATETIMEOFFSET`, `TIMESTAMP WITH TIME ZONE`),
  the result then depends on the session time zone, as it does for the bound parameter.
  The executed statement always binds its parameters, so the inline spelling never changes
  what a check measures.

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
    substitutes `&name` and, by default (`SQLBLANKLINES OFF`), a blank line ends the SQL
    being entered even inside a string literal, so the lines after it run as a new command
    (which is why no inlined literal holds a line terminator); SSMS and `sqlcmd` split batches at a line holding `GO`, and drivers
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
