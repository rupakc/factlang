# IR

> The IR (intermediate representation) is a small, dialect-free list of measurement primitives such as "aggregate these columns" or "overlap these keys".
> Each primitive declares how many rows it returns and what its result columns are called, so nothing assumes the first row.
> It lives in `factlang/ir/` (layer 8) and is public API for tools built on FactLang.

## Why an IR at all

A verb should say what to measure, not how to spell SQL for twelve warehouses. The verb produces a **ProbePlan** made of primitives; the SQL layer turns primitives into text. That split is also what lets the [[Optimizer]] fuse two plans into one query without knowing any SQL.

## The primitives

| Primitive | Measures | Returns |
|---|---|---|
| `ColumnAggregate` | metrics over one column or a whole table | one row |
| `CompositeKeyAggregate` | metrics over a tuple of key columns | one row |
| `ValueMembership` | how many values are outside a declared list, plus the top undeclared values | a summary row plus up to `report_limit` ranked rows |
| `KeyOverlap` | how left keys line up with right keys (single or composite) | one row |
| `PatternCoverage` | how many non-NULL values match a pattern | one row |
| `MetadataQuery` | column names and types for a table | rows |

Each primitive carries its table(s), columns and an optional filter. `KeyOverlap` also has an IR-only filter for the right side and a `cast_to` option.

## Metrics and their aliases

Metric result columns are a **wire format**; a conformance test pins them for every dialect:

`row_count`, `non_null`, `distinct_keys`, `max_rows_per_key`, `min_value`, `max_value`, `sum_value`, `avg_value`, `duplicate_keys`, `server_now`.

The name `rows` is never used because it is reserved in BigQuery. `KeyOverlap` returns `distinct_left`, `matched`, `distinct_right`, `left_non_null`, `matched_left_rows` and, when asked, `max_left_per_key`, `max_right_per_key`.

## NULL rules live here

So that every dialect inherits them:

- `MAX_PER_KEY` drops rows with any NULL key column before grouping.
- `DISTINCT_KEYS` over several columns groups by the columns; it never concatenates them.
- `non_null` over a tuple counts rows whose whole key is present.
- `PatternCoverage` compares case-folded and only over non-NULL values.
- `ValueMembership` skips NULLs and, with `split`, compares the trimmed text before the first separator.

## No sampling of inputs

No primitive samples its input unless its `sample` field is set, and only `Checker.measure` accepts such a plan ([[Measurement-API]]). The one row limit anywhere is `ValueMembership.report_limit`, which cuts the *reported list* after exact counting; the summary counts are always exact. A conformance test checks that `LIMIT`, `TOP`, `FETCH FIRST` and sampling text appear nowhere else.

## Extending the IR

A custom primitive registers with `fl.ir.register_primitive(cls, lower=fn)`. It must declare arity and result columns and supply a lowering to the internal SQL tree; otherwise rendering it is FL500.

## See also

- [[Optimizer]]
- [[Dialects]]
- [[Measurement-API]]
- [[Extending-Verbs]]
- [[Judge]]
