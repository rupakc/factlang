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
| `FL6xx` | contract completeness | reported by `fl.check_pack` in your tests |
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
| <a id="fl101"></a>FL101 | lexer | error | unterminated string or quoted identifier (it ends at the newline) | `VALUES orders.status IN ['paid]` |
| <a id="fl102"></a>FL102 | lexer | error | unexpected character, or a number that is invalid or too large | `ROWS orders VALUE >= 1 ;` |
| <a id="fl201"></a>FL201 | parser | error | expected a value (an attribute with nothing after it) | `ROWS orders VALUE` |
| <a id="fl202"></a>FL202 | parser | error | unclosed list | `ROWS orders VALUE >= [1,` |
| <a id="fl203"></a>FL203 | parser | error | unexpected token: no verb at the start of a line, a bad ref part, junk after `USE` | `rows orders VALUE >= 1` |
| <a id="fl204"></a>FL204 | parser | error | duplicate attribute | `ROWS orders VALUE >= 1 VALUE <= 9` |
| <a id="fl205"></a>FL205 | lexer | error | dangling line continuation (a `\` with no next line) | `ROWS orders VALUE >= 1 \` |
| <a id="fl206"></a>FL206 | parser | error | malformed filter: anything the filter grammar cannot read, an attribute after `WHERE`, `x = NULL`, a bare `TRUE` | `ROWS orders VALUE >= 1 WHERE status = NULL` |
| <a id="fl207"></a>FL207 | parser | error | nesting too deep: parentheses, `NOT` chains and lists share a budget of 64 levels | *a filter of 65 nested `NOT`s* |
| <a id="fl300"></a>FL300 | analyser | error | unknown verb, with a suggestion | `UNIQE orders.id` |
| <a id="fl301"></a>FL301 | analyser | error | wrong number of operands | `ROWS VALUE >= 1` |
| <a id="fl302"></a>FL302 | analyser | error | wrong operand kind (a table where a column is needed) or too many ref parts | `UNIQUE orders` |
| <a id="fl303"></a>FL303 | analyser | error | unknown attribute for this verb, with a suggestion | `UNIQUE orders.id MATCH >= 99%` |
| <a id="fl304"></a>FL304 | analyser | error | a required attribute is missing (or `WITH`/`TO` of a composite `JOIN` without the other) | `FRESH orders.id` |
| <a id="fl305"></a>FL305 | analyser | error | wrong value type for an attribute, or a bare filter atom on a column the catalog knows is not boolean | `ROWS orders VALUE >= 'many'` |
| <a id="fl306"></a>FL306 | analyser | error | operator not allowed for this attribute (or missing where one is required) | `FANOUT orders.id MAX_PER_KEY >= 3` |
| <a id="fl307"></a>FL307 | analyser | error | unusable column list (a repeated or redundant key column) | `UNIQUE orders.customer_id WITH [customer_id]` |
| <a id="fl308"></a>FL308 | analyser | error | a `WHERE` filter on a verb that does not accept one | `EXISTS orders WHERE id = 1` |
| <a id="fl309"></a>FL309 | analyser | error | invalid value: out of range, an invalid date, mutually exclusive attributes, `WITH`/`TO` of different lengths | `NULLS customers.email RATE <= 120%` |
| <a id="fl311"></a>FL311 | analyser | error | no such table or column, on a definite answer from the catalog | `UNIQUE orders.stauts` |
| <a id="fl312"></a>FL312 | analyser | warning | a `JOIN` between different type families without `CAST` | `JOIN orders.customer_id -> customers.email MATCH >= 99%` |
| <a id="fl313"></a>FL313 | analyser | info | names could not be verified: the catalog could not answer (reported once per claim) | *any claim checked with `NullCatalog`* |
| <a id="fl404"></a>FL404 | planning | error | a contract's plan sets `sample`: claims are always measured exactly | *a contract returning `ColumnAggregate(…, sample=SampleSpec(…))`* |
| <a id="fl405"></a>FL405 | planning | error | a `system`/`bernoulli` sample on a plan that reads the table more than once; use `keyhash` | *`ValueMembership(…, sample=SampleSpec("bernoulli", …))`* |
| <a id="fl500"></a>FL500 | rendering | error | a primitive cannot be rendered: not registered, a bad lowering, or a rendered text that fails the guard | *`fl.measure` of an unregistered primitive* |
| <a id="fl502"></a>FL502 | rendering | error | an identifier cannot be represented safely: NUL, newline, empty or longer than the dialect allows | *a 300-character quoted name on Snowflake* |
| <a id="fl503"></a>FL503 | rendering | error | a cast to a type family the dialect cannot express | *`JOIN … CAST json` on Oracle* |
| <a id="fl504"></a>FL504 | rendering | error | unknown dialect: no native profile and sqlglot not installed or unaware | *`fl.compile(…, dialect="nope")`* |
| <a id="fl505"></a>FL505 | rendering | error | too many table-name parts for the dialect | *`ROWS a.b.c VALUE >= 1` on MySQL* |
| <a id="fl506"></a>FL506 | rendering | error | the dialect cannot express the requested sample method; never a silent exact query or `LIMIT` | *a `system` sample on DuckDB* |
| <a id="fl507"></a>FL507 | rendering | error | literal cannot be inlined safely: `inline()` refuses a value (non-scalar, a subclass of a scalar type, non-finite, too many digits, a backslash/NUL/lone surrogate/line terminator, a `'` where the dialect has no doubled-quote form, or a type that does not match its family); display SQL falls back to the `{{name}}` template | *`probe.inline()` of `'a\b'`* |
| <a id="fl600"></a>FL600 | contract | error | `decide` reads a fact `derive` never declares | *`.decide(Compare("max", …))` with `.facts("min")`* |
| <a id="fl601"></a>FL601 | contract | error | `decide` reads an attribute the signature does not declare | *`Compare("x", ">", attr="LIMIT")` on a verb without `LIMIT`* |
| <a id="fl602"></a>FL602 | contract | error | fewer than three valid and three invalid examples | *a verb with two examples* |
| <a id="fl603"></a>FL603 | contract | error | an example does not behave as declared, or its plan or catalog function raises | *a "valid" example that reports `FL303`* |
| <a id="fl604"></a>FL604 | contract | error | an incomplete decorator-built contract (no plan, derive or decide) | *`@pack.verb(…)` with no `.derive`* |
| <a id="fl605"></a>FL605 | contract | error | a filterable verb's plan drops the claim's `WHERE` filter (it would measure the whole table) | *a plan that never passes `filter=claim.filter`* |
| <a id="fl700"></a>FL700 | execution | error | the query failed (the warehouse rejected it, the result had the wrong shape, the driver raised) | *a probe on a table that does not exist* |
| <a id="fl701"></a>FL701 | execution | error | over the cost budget: the dry run priced it above `max_bytes`, or the warehouse refused | *`Checker(…, max_bytes=10)` on BigQuery* |
| <a id="fl702"></a>FL702 | execution | error | timeout | *`Checker(…, timeout_s=0.001)` on a slow query* |
| <a id="fl703"></a>FL703 | execution | error | metadata SQL refused by the guard of `fl.run_metadata` | *`fl.run_metadata(c, "DROP TABLE orders")`* |

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
