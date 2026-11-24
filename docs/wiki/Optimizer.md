# Optimizer

> The optimizer looks at a batch of plans and removes repeated work: duplicate claims run once, and claims over the same table with the same filter share one scan.
> Results are split back so each claim still gets its own outcome, and the evidence lists which claims shared a query.
> It lives in `factlang/optimizer.py` (layer 13) and is on by default; pass `fuse=False` to turn fusion off.

## De-duplication

Claims with the same semantic hash in one batch are measured once and the outcome is reused. Writing the same fact twice, in different spelling, costs nothing extra.

## Fusion

Claims such as these all scan `orders`:

```
ROWS orders VALUE >=1000
NULLS orders.shipped_at RATE <=5%
UNIQUE orders.id
```

The optimizer merges their `ColumnAggregate` primitives into one `SELECT` with all the metrics, with aliases prefixed `m<i>__` so they cannot collide, runs it once, and splits the row back per claim. On a pay-per-byte warehouse this can turn many full scans into one.

Rules:

- Only primitives with the same table **and** the same filter fuse. Never across different filters, because that would change a meaning.
- `CompositeKeyAggregate` fuses the same way.
- Other primitives (`KeyOverlap`, `ValueMembership`, ...) run on their own.

## Measurements too

`Checker.measure` uses the same de-duplication and fusion across a batch of raw plans (identity is the plan's canonical form). See [[Measurement-API]].

## Cost guard

When the connector supports a dry run (BigQuery) and `max_bytes` is set, a probe over budget becomes an `error` outcome with FL701 and is never executed. `Checker.estimate` shows the price without running anything.

## See also

- [[IR]]
- [[Connectors]]
- [[Measurement-API]]
- [[Architecture]]
