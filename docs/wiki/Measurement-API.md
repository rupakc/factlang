# Measurement API

> Tools built on FactLang (schema profilers, catalogs, monitors) can run measurement primitives directly, with no claim and no verdict.
> This page covers raw measurement, cost estimates, filter and join fragments, metadata SQL, and the sampled measurement for very large tables.
> Everything here is public API under SemVer: no breaking change within a `0.x` minor series.

These capabilities were added in Amendment A and B (see [[Design-Decisions]]) so a library like SchemaGuru never has to write SQL or touch FactLang internals.

## Raw measurement

`Checker.measure(plans)` (and `ameasure`, and the one-call `fl.measure(plan_or_plans, connector=...)`) runs IR primitives that have no claim, through the same render, cost-guard and execute path, with fusion and de-duplication across the batch.

```python
import factlang as fl
from factlang import ColumnRef, EdgeRef, Ident, TableRef

orders = TableRef(None, None, Ident("orders"))        # catalog, schema, table
connector = fl.DBAPIConnector(conn, "duckdb")

m = fl.measure(fl.ir.ValueMembership(orders, Ident("status"), declared=(), report_limit=20),
               connector=connector)
print(m.payload)   # {"rows": [...]}: summary row plus ranked top values
print(m.error)     # None, or an FL700/FL701/FL702 Diagnostic; measure never raises
```

A `Measurement` holds `plan`, `payload`, `verdict_hint`, `evidence`, `error`. The payload is exactly what a contract's derive step would receive: a row dict for single-row plans, `{"rows": [...]}` for row-set plans, `None` for no rows.

## Top-k observed values

`ValueMembership` accepts an **empty** `declared` list. Then every value counts as undeclared and the ranked list is the exact top-k most frequent values (`report_limit` up to 1000), ranked by frequency descending then value ascending. NULLs are excluded. The `VALUES t.c IN []` claim behaves as before.

## Composite joins and reverse facts

`KeyOverlap` takes key tuples on both sides and returns forward and reverse facts (`distinct_left`, `matched`, `distinct_right`, `left_non_null`, `matched_left_rows`), so you can see an asymmetric join (every order has a customer but only 1% of customers have orders). It has an IR-only `right_filter`. See the JOIN verb in [[Standard-Verbs]].

## Estimating cost without running

```python
checker = fl.Checker(connector)
est = checker.estimate("JOIN orders.customer_id -> customers.id MATCH >=99%")
# [CostEstimate(semantic_hash_or_plan_key, sql, bytes, method="dry_run" | "unknown")]  (`.key` is an alias)
```

`estimate` accepts claim text or plans. Connectors without a dry run return `bytes=None, method="unknown"`; nothing is executed and nothing is guessed.

## Filter and join fragments

```python
items = TableRef(None, None, Ident("order_items"))
customers = TableRef(None, None, Ident("customers"))
edge1 = EdgeRef(ColumnRef(items, Ident("order_id")), ColumnRef(orders, Ident("id")))
edge2 = EdgeRef(ColumnRef(orders, Ident("customer_id")), ColumnRef(customers, Ident("id")))

f, diags = fl.parse_filter("deleted IS NULL OR NOT deleted")
frag = fl.sql.render_filter(f, table=orders, dialect="snowflake")
snippet = fl.sql.render_join([edge1, edge2], dialect="bigquery", filters={items: f})
print(frag.inline())      # display-oriented text; FL507 for a literal it cannot render safely
```

A `RenderedFragment` has `sql_template`, `params`, `bind(paramstyle)` and `inline()`, like a probe, but is not a full statement and is never executed by FactLang. In `render_join` the left-most table's filter goes in `WHERE`, other tables' filters in their `ON`. Hostile literals appear only in params.

## Metadata SQL

`fl.run_metadata(connector, sql, params=())` runs guarded catalog queries (FL703 on refusal). See [[Connectors]].

```python
rows = fl.run_metadata(connector, "SELECT table_name FROM information_schema.tables").rows
```

## Sampled measurement (Amendment B)

For terabyte tables an exact scan may be unaffordable. A `SampleSpec` lets `Checker.measure` run a primitive on a sample:

```python
from decimal import Decimal

spec = fl.ir.SampleSpec(method="keyhash", fraction=Decimal("0.5"), seed=7,
                        key=(Ident("id"),))   # method: "keyhash", "system" or "bernoulli"
plan = fl.ir.ColumnAggregate(orders, None, [fl.Metric.ROW_COUNT], sample=spec)
m = fl.measure(plan, connector=connector)
print(m.payload, m.evidence.method)   # raw counts on the sample; method "sampled:keyhash:0.5:7"
```

The rules that keep this safe:

1. **Measurement only, never verdicts.** Claims, contracts and the analyzer never produce a sampled plan. A sampled plan in a claim is **FL404**.
2. **`LIMIT` is never a sampling method.** (A LIMIT "sample" once overstated a rate more than three times, because it returns rows in storage order.)
3. **No silent fallback.** A dialect that cannot express a method raises **FL506**; it never quietly runs exact or with LIMIT. Fraction 1 renders no sample.
4. **`system` and `bernoulli` are single-read only.** A plan that reads the table more than once (value membership, key overlap, keyed metrics) cannot be sampled this way, because a block or row sample is not guaranteed to repeat; that is **FL405**, and the message says to use keyhash.
5. **`keyhash` is deterministic**: it hashes the key (with the seed) into 1,000,000 buckets and keeps rows below the cutoff. Same seed and same data give the same sample, and with the same seed and fraction on both sides of a join, both sides keep exactly the same key values. Consistency holds within one dialect; samples are not comparable across dialects. Rows with a NULL key part are excluded first.
6. **Raw numbers only.** Results are the raw metrics on the sampled rows plus `sampled_rows` (or `sampled_left_rows` and `sampled_right_rows` for joins). FactLang does not scale or estimate; the host does. Evidence records `method = "sampled:<method>:<fraction>:<seed>"`, and exact runs record `"exact"`.

`Checker.estimate` prices sampled plans too (dry-run bytes reflect block sampling where the warehouse does). `MetadataQuery` is never sampled.

## See also

- [[IR]]
- [[Connectors]]
- [[Optimizer]]
- [[Design-Decisions]]
- [[Diagnostics]]
