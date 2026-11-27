---
template: home.html
title: FactLang
hide:
  - navigation
  - toc
---

<!-- The landing page is docs/overrides/home.html. This file is its quick start: the test
suite runs both python blocks in order (tests/conformance/test_docs_examples.py), checks
that the first is the Getting started shop and that the output shown is what they print
(tests/conformance/test_landing_facts.py). -->

<div class="fl-head" markdown>
<span class="fl-eyebrow">Quick start</span>

## From install to verdicts in three steps

The shop from [Getting started](getting-started.md), in an in-memory DuckDB database: no
warehouse needed.
{: .fl-head__sub }

</div>

<div class="fl-qs" markdown>
<div class="fl-qs__step" markdown>
<span class="fl-qs__num" aria-hidden="true">1</span>
<div class="fl-qs__body" markdown>

### Install

The core has no dependencies; the `duckdb` extra adds the database this example uses.

```bash
pip install "factlang[duckdb]"
```

</div>
</div>
<div class="fl-qs__step" markdown>
<span class="fl-qs__num" aria-hidden="true">2</span>
<div class="fl-qs__body" markdown>

### Build a shop to check

500 customers and 2,000 orders, with a few things wrong on purpose.

```python
import duckdb
import factlang as fl

conn = duckdb.connect()
conn.execute("""
    CREATE TABLE customers AS
    SELECT i AS id,
           CASE WHEN i % 25 = 0 THEN NULL ELSE 'customer' || i || '@example.com' END AS email
    FROM range(1, 501) AS t(i)
""")
conn.execute("""
    CREATE TABLE orders AS
    SELECT i AS id,
           CASE WHEN i % 160 = 0 THEN 10000 + i ELSE 1 + (i * 7) % 500 END AS customer_id,
           CASE i % 10 WHEN 0 THEN 'refunded' WHEN 1 THEN 'open' WHEN 2 THEN 'open'
                       ELSE 'paid' END AS status,
           CAST(5 + (i * 37) % 200 AS DECIMAL(10, 2)) AS amount,
           CASE WHEN i % 10 IN (1, 2) THEN NULL
                ELSE current_date - CAST(i % 30 AS INTEGER) END AS shipped_at
    FROM range(1, 2001) AS t(i)
""")

connector = fl.DBAPIConnector(conn, "duckdb")
```

</div>
</div>
<div class="fl-qs__step" markdown>
<span class="fl-qs__num" aria-hidden="true">3</span>
<div class="fl-qs__body" markdown>

### Check what an analyst said

```python
report = fl.Checker(connector).check("""
JOIN orders.customer_id -> customers.id CARD N:1 MATCH >=99%
NULLS customers.email RATE <=5%
VALUES orders.status IN ['paid', 'open', 'shipped']
""")
for outcome in report.outcomes:
    print(f"{outcome.verdict.value:5} {outcome.explanation}")
```

```text
fail  match_rate 0.9765625 < 0.99
pass  null_rate 0.04 <= 0.05
fail  undeclared_rows 200 != 0
```

</div>
</div>
</div>
