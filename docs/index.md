---
template: home.html
title: FactLang
hide:
  - navigation
  - toc
---

<div class="fl-section" markdown>

## Prose cannot be checked. Claims can.

<p class="fl-lead">AI agents and analysts answer questions about data in prose. A wrong
sentence looks exactly like a right one, so it survives, and every answer built on it is
quietly wrong. FactLang turns each factual sentence into one line that a machine can
measure.</p>

<div class="fl-trio" markdown>
<div markdown>

### The answer said

<ul>
<li>"There are <mark>1,400 paid orders</mark>."</li>
<li>"<mark>Every order belongs to a customer</mark>."</li>
<li>"An order's status is <mark>paid, open or shipped</mark>."</li>
</ul>

</div>
<div markdown>

### As FactLang claims

```factlang
COUNT orders VALUE ==1400 WHERE status = 'paid'
JOIN orders.customer_id -> customers.id MATCH >=99%
VALUES orders.status IN ['paid', 'open', 'shipped']
```

</div>
<div markdown>

### What the data says

<ul>
<li><span class="fl-verdict fl-verdict--pass">pass</span> value 1400 == 1400</li>
<li><span class="fl-verdict fl-verdict--fail">fail</span> match_rate 0.9765625 &lt; 0.99</li>
<li><span class="fl-verdict fl-verdict--fail">fail</span> undeclared_rows 200 != 0, the
most common being <code>refunded</code></li>
</ul>

</div>
</div>

Every verdict carries its **evidence**: the numbers it was decided on, the exact SQL with its
bind parameters, the dialect and how much that dialect is trusted. A claim FactLang cannot
measure exactly is reported as such, never approximated.

</div>

<div class="fl-section" markdown>

## Where it shines

<div class="fl-uses" markdown>
<div class="fl-use" markdown>
<div markdown>

### Agent answer verification

Give your agent the FactLang tool definitions. It writes the numbers in its answer as
claims, FactLang measures them, and only verified sentences reach the user.

</div>

```factlang
COUNT opportunities VALUE ==1717 WHERE stage = 'Churned'
```

</div>
<div class="fl-use" markdown>
<div markdown>

### Schema and registry validation

A schema registry says which keys join and which values a column takes. FactLang checks
every one of those statements against the warehouse, nightly.

</div>

```factlang
JOIN order_items.order_id -> orders.id CARD N:1 MATCH >=99%
UNIQUE order_items.order_id WITH [line_no]
```

</div>
<div class="fl-use" markdown>
<div markdown>

### Data contracts in CI

Keep the contract in a `.fl` file next to the code. `factlang check` exits non-zero when a
claim fails, so a broken contract fails the build.

</div>

```factlang
NULLS customers.email RATE <=5%
FRESH events.created_at WITHIN 24h
```

</div>
<div class="fl-use" markdown>
<div markdown>

### Migration parity checks

Run the same claims on the old warehouse and the new one. The claims do not change; only
the dialect does, so the two reports compare like for like.

</div>

```factlang
ROWS orders VALUE ==2000
SUM orders.amount VALUE ==209000 ABS_TOLERANCE 0.01
```

</div>
<div class="fl-use" markdown>
<div markdown>

### LLM evaluation grounding

Score a model's answers by whether their facts hold, not by string similarity to a
reference answer. Each claim has a semantic hash, so the same fact is scored once.

</div>

```factlang
MAX orders.amount VALUE <=204
DISTINCT orders.customer_id VALUE ==512
```

</div>
</div>

</div>

<div class="fl-section" markdown>

## Built to be trusted

<div class="fl-promises" markdown>

**Every row, never a sample.** Claims are always judged on all rows that match their
filter. Sampling exists only for raw measurement of very large tables, and never produces a
verdict.

**Read-only by construction.** A probe is one `SELECT`. Every literal you write becomes a
typed bind parameter, so no claim can carry SQL.

**Undecidable stays undecidable.** A fact the data cannot support is `no_data`, never a
pass. A warehouse that rejects the query is `error`, never `no_data`.

**Diagnostics you can act on.** Stable codes, carets under the problem and "did you mean"
suggestions. A file with five mistakes reports five diagnostics.

**Eleven native dialects.** BigQuery, Snowflake, Redshift, Postgres, DuckDB, Databricks,
ClickHouse, SQL Server, MySQL, Oracle and Trino, plus any dialect sqlglot can write.

**A core with no dependencies.** The language, the planner and every dialect are pure
standard-library Python. That is why the [playground](playground.md) runs in your browser.

</div>

</div>

<div class="fl-section" markdown>

## Thirty seconds

```python
import duckdb
import factlang as fl

conn = duckdb.connect()
conn.execute("CREATE TABLE orders AS SELECT range AS id FROM range(1000)")

outcome = fl.check("UNIQUE orders.id", connector=fl.DBAPIConnector(conn, "duckdb"))
print(outcome.verdict.value, "-", outcome.explanation)   # pass - max_rows_per_key 1 <= 1
```

[Get started](getting-started.md){ .fl-button .fl-button--solid }
[Read the language guide](guide/language.md){ .fl-button }

</div>
