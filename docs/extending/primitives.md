# IR primitives

Between a claim and its SQL sits a small, dialect-free **intermediate representation**: a
handful of measurement primitives. A verb's plan returns one; the dialect layer renders it;
the engine shapes the rows by the primitive's declared **arity** (one row, or a row set)
instead of assuming `rows[0]`. Every code block on this page runs in the test suite.

## The core set

| primitive | fields | arity | result columns |
|---|---|---|---|
| `ColumnAggregate` | `table`, `column` (or `None` for table metrics), `metrics`, `filter`, `sample` | single row | the metric aliases |
| `CompositeKeyAggregate` | `table`, `columns`, `metrics`, `filter`, `sample` | single row | the metric aliases |
| `ValueMembership` | `table`, `column`, `declared` (text; may be empty), `split`, `report_limit` (default 20, max 1000), `filter`, `sample` | row set | one `kind='summary'` row (`non_null`, `declared_rows`, `undeclared_distinct`) plus up to `report_limit` `kind='undeclared'` rows (`value`, `freq`), by count desc, value asc |
| `KeyOverlap` | `left`, `right` (equal-length tuples of column refs, one table per side), `cast_to`, `measure_multiplicity`, `filter` (left), `right_filter`, `sample`, `right_sample` | single row | `distinct_left`, `matched`, `distinct_right`, `left_non_null`, `matched_left_rows` (+ `max_left_per_key`, `max_right_per_key`) |
| `PatternCoverage` | `table`, `column`, `pattern`, `filter`, `sample` | single row | `non_null`, `matching` |
| `MetadataQuery` | `table`, `column` | row set | `column_name`, `data_type` |

All are frozen dataclasses in `factlang.ir`, as are `Metric`, `SampleSpec` and `Arity`.

## Wire format

Metric result columns are a **wire format**, pinned by a conformance test on every dialect:

| metric | alias |
|---|---|
| `ROW_COUNT` | `row_count` |
| `NON_NULL` | `non_null` |
| `DISTINCT_KEYS` | `distinct_keys` |
| `MAX_PER_KEY` | `max_rows_per_key` |
| `MIN_VALUE` | `min_value` |
| `MAX_VALUE` | `max_value` |
| `SUM` | `sum_value` |
| `AVG` | `avg_value` |
| `DUPLICATE_KEYS` | `duplicate_keys` |
| `NOW` | `server_now` |

(Never `rows`: it is reserved in BigQuery.)

## NULL rules live in the IR

So every dialect inherits them: `MAX_PER_KEY` excludes rows with any NULL key column before
grouping; `DISTINCT_KEYS` over several columns groups by them and never concatenates;
`NON_NULL` over a tuple counts rows with a complete key; `PatternCoverage` matches
case-folded over non-NULL values; `ValueMembership` excludes NULLs and, with `split`, compares
the trimmed prefix before the first separator.

No primitive limits or samples its input unless its `sample` field is set, which only raw
[measurement](../guide/measure.md) accepts. The one permitted limit is
`ValueMembership.report_limit`, which truncates the ranked list of undeclared values after
exact aggregation.

```python
import factlang as fl
from factlang.semantics.refs import Ident, TableRef

orders = TableRef(None, None, Ident("orders"))
plan = fl.ir.ColumnAggregate(orders, Ident("amount"), (fl.Metric.SUM, fl.Metric.NON_NULL))
print(plan.result_columns())          # ('sum_value', 'non_null')
probe = fl.sql.renderer_for("snowflake")(plan)
print(probe.inline())                 # SELECT SUM(amount) AS sum_value, COUNT(amount) AS non_null FROM orders
assert plan.result_columns() == ("sum_value", "non_null")
```

## A custom primitive

A plugin primitive is a frozen dataclass with a class attribute `arity` and a
`result_columns()` method, registered together with a **lowering** into the internal SQL AST
(`factlang.sql.ast`). Because it is lowered into the same tree as the core primitives, every
native profile and the sqlglot bridge can render it, and user values can only enter through
`ParamRef` bind parameters.

```python
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

from factlang.semantics.filters import Param
from factlang.sql import ast as A

@dataclass(frozen=True)
class CountAbove:
    """Rows whose column exceeds a threshold."""
    arity: ClassVar[fl.ir.Arity] = fl.ir.Arity.SINGLE_ROW
    table: TableRef
    column: Ident
    threshold: Decimal

    def result_columns(self):
        return ("above",)

def lower_count_above(plan):
    above = A.Compare(">", A.Column(plan.column),
                      A.ParamRef(Param("p1", plan.threshold, "decimal")))
    return A.Select((A.Aliased(A.CountIf(above), "above"),), A.TableName(plan.table))

fl.ir.register_primitive(CountAbove, lower=lower_count_above, replace=True)

probe = fl.sql.renderer_for("bigquery")(CountAbove(orders, Ident("amount"), Decimal("100")))
print(probe.inline())    # SELECT COALESCE(COUNTIF(amount > 100), 0) AS above FROM orders
assert probe.sql_template.count("{{p1}}") == 1
```

A lowering that returns something other than a `Select`/`UnionAll`, or selects columns other
than `result_columns()`, is refused with `FL500`, as is rendering a primitive nobody
registered. Use it from a [verb's plan](verbs.md) or [measure](../guide/measure.md) it
directly.

## See also

- [Raw measurement](../guide/measure.md)
- [Dialects](dialects.md)
