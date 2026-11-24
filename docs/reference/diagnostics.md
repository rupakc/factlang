# Diagnostics

Every problem FactLang reports carries a stable code `FLnnn`. The hundreds digit is the
stage that found it:

| range | stage | surfaces as |
|---|---|---|
| `FL1xx` | lexer | a diagnostic; the claim is `invalid` |
| `FL2xx` | parser | a diagnostic; the claim is `invalid` |
| `FL3xx` | analyser | a diagnostic; the claim is `invalid` (warnings and info do not invalidate) |
| `FL4xx` | planning | an `error` outcome, or a `ValueError` from `plan_for` / `check_pack` |
| `FL5xx` | rendering | `RenderError`; an `error` outcome in a report, a `ValueError` from `fl.compile` |
| `FL6xx` | contract completeness | reported by `factlang.contract.check_pack` in your tests |
| `FL7xx` | execution | `ExecutionError`; an `error` outcome, or `Measurement.error` |

A diagnostic has a `code`, a `severity` (`error`, `warning`, `info`), a `message`, a `span`
(line, column, end column), an optional `hint` and an optional `suggestion` (did you mean).
`DiagnosticBag.render()` prints caret text and `to_list()` the JSON form:

```text
error[FL300]: unknown verb UNIQE
  --> 1:1
UNIQE orders.id
^^^^^
did you mean: UNIQUE?
```

This table lists **every** code the library emits; a test asserts that it matches the
source exactly. Examples in the FL1xx–FL3xx rows are executed by that test too, against a
catalog holding `orders(id, customer_id, status)` and `customers(id, email)`.

| code | stage | severity | meaning | example |
|---|---|---|---|---|
| FL101 | lexer | error | unterminated string or quoted identifier (it ends at the newline) | `VALUES orders.status IN ['paid]` |
| FL102 | lexer | error | unexpected character, or a number that is invalid or too large | `ROWS orders VALUE >= 1 ;` |
| FL201 | parser | error | expected a value (an attribute with nothing after it) | `ROWS orders VALUE` |
| FL202 | parser | error | unclosed list | `ROWS orders VALUE >= [1,` |
| FL203 | parser | error | unexpected token: no verb at the start of a line, a bad ref part, junk after `USE` | `rows orders VALUE >= 1` |
| FL204 | parser | error | duplicate attribute | `ROWS orders VALUE >= 1 VALUE <= 9` |
| FL205 | lexer | error | dangling line continuation (a `\` with no next line) | `ROWS orders VALUE >= 1 \` |
| FL206 | parser | error | malformed filter: anything the filter grammar cannot read, an attribute after `WHERE`, `x = NULL`, a bare `TRUE` | `ROWS orders VALUE >= 1 WHERE status = NULL` |
| FL207 | parser | error | nesting too deep: parentheses, `NOT` chains and lists share a budget of 64 levels | *a filter of 65 nested `NOT`s* |
| FL300 | analyser | error | unknown verb, with a suggestion | `UNIQE orders.id` |
| FL301 | analyser | error | wrong number of operands | `ROWS VALUE >= 1` |
| FL302 | analyser | error | wrong operand kind (a table where a column is needed) or too many ref parts | `UNIQUE orders` |
| FL303 | analyser | error | unknown attribute for this verb, with a suggestion | `UNIQUE orders.id MATCH >= 99%` |
| FL304 | analyser | error | a required attribute is missing (or `WITH`/`TO` of a composite `JOIN` without the other) | `FRESH orders.id` |
| FL305 | analyser | error | wrong value type for an attribute, or a bare filter atom on a column the catalog knows is not boolean | `ROWS orders VALUE >= 'many'` |
| FL306 | analyser | error | operator not allowed for this attribute (or missing where one is required) | `FANOUT orders.id MAX_PER_KEY >= 3` |
| FL307 | analyser | error | unusable column list (a repeated or redundant key column) | `UNIQUE orders.customer_id WITH [customer_id]` |
| FL308 | analyser | error | a `WHERE` filter on a verb that does not accept one | `EXISTS orders WHERE id = 1` |
| FL309 | analyser | error | invalid value: out of range, an invalid date, mutually exclusive attributes, `WITH`/`TO` of different lengths | `NULLS customers.email RATE <= 120%` |
| FL311 | analyser | error | no such table or column, on a definite answer from the catalog | `UNIQUE orders.stauts` |
| FL312 | analyser | warning | a `JOIN` between different type families without `CAST` | `JOIN orders.customer_id -> customers.email MATCH >= 99%` |
| FL313 | analyser | info | names could not be verified: the catalog could not answer (reported once per claim) | *any claim checked with `NullCatalog`* |
| FL400 | planning | none | not a diagnostic: the boundary the CLI uses to tell front-end codes (below FL400) from later ones | *never emitted* |
| FL404 | planning | error | a contract's plan sets `sample`: claims are always measured exactly | *a contract returning `ColumnAggregate(…, sample=SampleSpec(…))`* |
| FL405 | planning | error | a `system`/`bernoulli` sample on a plan that reads the table more than once; use `keyhash` | *`ValueMembership(…, sample=SampleSpec("bernoulli", …))`* |
| FL500 | rendering | error | a primitive cannot be rendered: not registered, a bad lowering, or a rendered text that fails the guard | *`fl.measure` of an unregistered primitive* |
| FL502 | rendering | error | an identifier cannot be represented safely: NUL, newline, empty or longer than the dialect allows | *a 300-character quoted name on Snowflake* |
| FL503 | rendering | error | a cast to a type family the dialect cannot express | *`JOIN … CAST json` on Oracle* |
| FL504 | rendering | error | unknown dialect: no native profile and sqlglot not installed or unaware | *`fl.compile(…, dialect="nope")`* |
| FL505 | rendering | error | too many table-name parts for the dialect | *`ROWS a.b.c VALUE >= 1` on MySQL* |
| FL506 | rendering | error | the dialect cannot express the requested sample method; never a silent exact query or `LIMIT` | *a `system` sample on DuckDB* |
| FL600 | contract | error | `decide` reads a fact `derive` never declares | *`.decide(Compare("max", …))` with `.facts("min")`* |
| FL601 | contract | error | `decide` reads an attribute the signature does not declare | *`Compare("x", ">", attr="LIMIT")` on a verb without `LIMIT`* |
| FL602 | contract | error | fewer than three valid and three invalid examples | *a verb with two examples* |
| FL603 | contract | error | an example does not behave as declared, or its plan or catalog function raises | *a "valid" example that reports `FL303`* |
| FL604 | contract | error | an incomplete decorator-built contract (no plan, derive or decide) | *`@pack.verb(…)` with no `.derive`* |
| FL605 | contract | error | a filterable verb's plan drops the claim's `WHERE` filter (it would measure the whole table) | *a plan that never passes `filter=claim.filter`* |
| FL700 | execution | error | the query failed (the warehouse rejected it, the result had the wrong shape, the driver raised) | *a probe on a table that does not exist* |
| FL701 | execution | error | over the cost budget: the dry run priced it above `max_bytes`, or the warehouse refused | *`Checker(…, max_bytes=10)` on BigQuery* |
| FL702 | execution | error | timeout | *`Checker(…, timeout_s=0.001)` on a slow query* |
| FL703 | execution | error | metadata SQL refused by the guard of `fl.run_metadata` | *`fl.run_metadata(c, "DROP TABLE orders")`* |

## Notes on particular codes

`FL206`
:   A filter is always the last element of a claim, so an attribute written after
    `WHERE` is read as part of the filter and fails here. Write attributes first.

`FL311` versus `FL313`
:   A catalog answering "unknown" never produces an error: only a definite "no such
    table/column" is `FL311`. `FL313` is information that names were not verified.

`FL404`, `FL405`, `FL506`
:   The sampling rules of [raw measurement](../guide/measure.md#sampling-very-large-tables):
    sampling never reaches a claim, block and row samples need a single read, and a dialect
    that cannot sample a way says so.

`FL605`
:   Without this check, a custom verb that forgets `filter=claim.filter` would answer
    `COUNT orders VALUE ==1400 WHERE status = 'paid'` with the count of *all* orders.

`FL703`
:   The guard is lexer-free and deliberately over-strict, trusts the connector's dialect
    label, and does not detect side-effecting functions; credentials are the real boundary.
    See [Metadata SQL](../guide/measure.md#metadata-sql).

`FL7xx` are never `no_data`
:   A query that could not run is a defect to fix, not an absence of data.
