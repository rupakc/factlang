# Batching and fusion

FactLang is built for warehouses where a scan costs money and a catalog has hundreds of
thousands of schemas. A `Checker` checks a whole document at once and does as little work
as it can without ever giving up exactness.

```python
import factlang as fl

checker = fl.Checker(
    connector,          # any Connector; or dialect="…" alone to compile without one
    catalog=None,       # default: CachedCatalog(WarehouseCatalog(connector))
    concurrency=4,      # probes in flight at once
    timeout_s=60,       # per probe; a timeout is an `error` outcome with FL702
    max_bytes=None,     # cost budget, enforced through the connector's dry run
    fuse=True,          # share scans between claims (see below)
)
report = checker.check(open("facts.fl").read())
```

## De-duplication

Claims with the same [semantic hash](language.md#canonical-form-and-identity) are measured
once, even when spelled differently or written under different `USE` lines. Each still gets
its own outcome.

## Fusion

Aggregate claims over the **same table with the same filter** are fused into one `SELECT`
that computes all their metrics, with aliases prefixed `m<i>__`, and the result row is split
back per claim. These three claims are one scan:

```factlang
ROWS orders VALUE >=1
NULLS orders.shipped_at RATE <=50%
MAX orders.amount VALUE <=204
```

```sql
SELECT COUNT(*) AS m0__row_count, COUNT(*) AS m1__row_count,
       COUNT(shipped_at) AS m1__non_null, MAX(amount) AS m2__max_value
FROM orders
```

Each outcome's `evidence.fused_with` lists the semantic hashes of its scan partners, and its
`evidence.sql` is the fused statement that actually ran. Claims with different filters are
never fused, so no claim's numbers can be affected by another claim's rows. `fuse=False`
gives every claim its own query.

## Concurrency, timeouts and cost

Probes run `concurrency` at a time, on a thread pool for `check` and an asyncio semaphore for
`acheck` (`await checker.acheck(text)`, with an `AsyncConnector`). A connector that cannot
enforce `timeout_s` itself is backstopped: no answer a few seconds after the timeout is
reported as `FL702` and abandoned.

With `max_bytes` set and a connector that supports a dry run (BigQuery does), each probe is
priced first; a probe over budget is never executed and becomes an `error` outcome with
`FL701`. To price a document without running anything, ask for an estimate:

```python
for estimate in checker.estimate("JOIN orders.customer_id -> customers.id MATCH >=99%"):
    print(estimate.key, estimate.method, estimate.bytes)
```

A connector without a dry run returns `bytes=None, method="unknown"`; FactLang never guesses
a price.

## Catalogs never enumerate

Name checks and the structure verbs ask the catalog about the tables your claims mention,
one table at a time, and cache the answers. Nothing ever lists every schema. See
[Catalogs](../extending/catalogs.md).

## See also

- [Evidence](evidence.md)
- [Raw measurement and sampling](measure.md)
