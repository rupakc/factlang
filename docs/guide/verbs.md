# Standard verbs

The standard pack (`standard`, version `1.0`) has seventeen verbs in five groups. This page
explains what each one measures and when it passes; the generated
[verb reference](../reference/verbs.md) lists every attribute, operator and example.

Each verb is one `Contract`: a plan (the measurement), a `derive` step that turns measured
rows into named **facts**, and a `decide` predicate over those facts. A fact the data cannot
support is `None`, and a predicate over `None` is undecidable, so the verdict is `no_data`,
never a pass.

## Structure facts

Answered from the catalog; no data is scanned.

```factlang
EXISTS orders
EXISTS orders COLUMN shipped_at
TYPE orders.amount IS decimal
```

`EXISTS t COLUMN c` names a column with an attribute rather than as `t.c`, because `a.b`
could equally be schema `a`, table `b`. `TYPE` compares the column's **type family**, one of
`integer decimal float string boolean date timestamp time bytes json array struct other`.
Each dialect maps its native type names onto these families (wrappers such as `Nullable(…)`
are unwrapped first, and a scale-0 decimal such as `NUMBER(38,0)` is an `integer`).

When the catalog cannot answer (no catalog, or the warehouse would not say), the verdict is
`no_data` with "catalog could not answer": never a pass, never a fail.

## Data-quality facts

Measured over every row that matches the claim's filter.

| verb | example | passes when |
|---|---|---|
| `ROWS` | `ROWS orders VALUE >=1000` | `row_count` satisfies the comparison |
| `NULLS` | `NULLS orders.shipped_at RATE <=5%` | `null_rate` satisfies the comparison |
| `DEAD` | `DEAD orders.legacy_flag` | every value is NULL (`null_rate == 1`) |
| `UNIQUE` | `UNIQUE order_items.order_id WITH [line_no]` | no key is held by more than one row |
| `FANOUT` | `FANOUT order_items.order_id MAX_PER_KEY <=50` | the busiest key's row count satisfies the comparison |
| `VALUES` | `VALUES orders.status IN ['paid', 'open']` | every non-NULL value is declared |
| `RANGE` | `RANGE orders.amount BETWEEN [0, 10000]` | min and max lie inside the closed range |
| `PATTERN` | `PATTERN customers.email LIKE '%@%.%' RATE >=99%` | the share matching satisfies the comparison |
| `FRESH` | `FRESH events.created_at WITHIN 24h` | the newest value is no older than the duration |

The exact definitions, because these are the product:

`null_rate`
:   `(row_count − non_null) / row_count`; `None` (so `no_data`) for an empty table.

`UNIQUE` and `FANOUT`
:   Rows with a NULL in any key column are excluded before grouping. A composite key
    (`WITH [...]`) is grouped by its columns, never concatenated. `duplicate_keys` counts the
    distinct keys held by more than one row; `max_rows_per_key` is the busiest key's count.

`VALUES`
:   The column is compared **as text**, case-sensitively, after `TRIM`; numbers in the list
    are written in canonical form (`1`, `2.5`). NULLs are excluded. `SPLIT ':'` compares only
    the part before the first separator, so `other: customer moved` counts as `other`.
    `ALLOW <=1%` tolerates a share of undeclared rows. Facts include `undeclared_rows`,
    `coverage` (declared rows / non-NULL rows) and `top_undeclared`, the most frequent
    undeclared values with their counts (at most 20, ranked by count then value; the counts
    themselves are exact). An empty list, `IN []`, declares nothing, so every value is
    undeclared and `top_undeclared` is the exact top of the column.

`RANGE`
:   Bounds are numbers or typed dates/timestamps, `[low, high]` inclusive. `DATE` bounds over
    a `TIMESTAMP` column mean the half-open UTC interval `[low 00:00Z, high + 1 day 00:00Z)`,
    so `BETWEEN [DATE '2025-01-01', DATE '2025-01-31']` includes every instant of 31 January.

`PATTERN`
:   SQL `LIKE` (`%` and `_`), case-folded on both sides, over non-NULL values:
    `match_rate = matching / non_null`.

`FRESH`
:   One query returns the column's maximum and the server's clock; Python computes
    `age_seconds`. A `TIMESTAMP` without zone and a `DATE` are read as UTC (a date is its
    midnight). A non-temporal column gives `no_data` ("not a temporal value"); an empty
    table gives `no_data`.

## Relationship facts

```factlang
JOIN orders.customer_id -> customers.id CARD N:1 MATCH >=99%
JOIN order_items.order_id -> orders.order_id WITH [shop_id] TO [shop_id] MATCH >=99%
JOIN events.session_id -> sessions.id MATCH >=99% COVERAGE >=50% CAST string
```

`MATCH` is decided on `match_rate`: matched distinct non-NULL left keys over distinct
non-NULL left keys. NULL left keys are excluded; their share is a `NULLS` claim, not a join
claim. Without `CAST`, keys are compared in their native types, and the analyser warns
(`FL312`) when the catalog shows two different type families; `CAST <family>` casts every
key part on both sides.

Beyond `match_rate`, a `JOIN` reports:

- `coverage`: matched distinct right keys over distinct non-NULL right keys, how much of the
  right side the left side reaches. `COVERAGE >=50%` decides on it.
- `row_match_rate`: left rows whose key matched, over left rows with a key.
- `fan_in`: left rows per distinct left key.
- `distinct_left`, `distinct_right`, `matched`, `left_non_null`, `matched_left_rows`.
- `max_left_per_key` and `max_right_per_key` when `CARD` is given. `CARD N:1` requires every
  right key to occur at most once, `1:1` both sides, `1:N` the left side.

All of them are `None`, never 0, when undecidable (for example an empty right side).
Composite keys use `WITH [...]` for further left columns and `TO [...]` for the matching
right columns; both must be present (`FL304`) with equal lengths (`FL309`). A key tuple with a
NULL part is excluded on its side. A filter on `JOIN` applies to the left table only.

## Answer facts

These are the numbers agents put in answers.

```factlang
COUNT opportunities VALUE ==1717 WHERE stage = 'Churned'
DISTINCT orders.customer_id VALUE ==512
SUM orders.amount VALUE ==209000 TOLERANCE 1%
AVG orders.amount VALUE ==104.5 ABS_TOLERANCE 0.01
MIN orders.amount VALUE >=0
MAX orders.placed_on VALUE <=DATE '2026-12-31'
```

`TOLERANCE` is relative to the stated value (`1%` of it); `ABS_TOLERANCE` is an absolute
band; they are mutually exclusive. With neither, the comparison is exact:

- Results arrive from the warehouse as `Decimal` where the driver allows, and are compared
  as `Decimal`, **exactly**: `SUM … VALUE ==0.3` means 0.3, not 0.30000000000000004.
- When either side is a float (a `DOUBLE` column, or a driver that returns floats), `==` uses
  `math.isclose(rel_tol=1e-9, abs_tol=1e-9)`, because the float was never exact to begin with.

`SUM` and `AVG` over no rows give `value = None`, so `no_data`, never 0. `MIN` and `MAX`
accept typed dates and timestamps as well as numbers.

!!! note "SQL Server and `SUM`/`AVG`"
    On `tsql`, `SUM` stays native: an `INT` sum beyond 2^31 is an arithmetic-overflow
    error, reported as an `error` verdict, never a wrong number (widening it would change a
    digit of some `DECIMAL`s). `AVG` is taken over `x + 0.0` because an integer `AVG`
    truncates; on a `DECIMAL(38, s>0)` that costs one fractional digit. Counts use
    `COUNT_BIG`.

## Semantic facts

```factlang
NOTE orders.amount IS eur BECAUSE 'all prices are stored in euro'
```

A fact no query can establish. It parses, is analysed, gets a semantic hash and is reported
as `unprobeable` with its reason. It is the reference example for modelling your own
semantic facts.

## See also

- [Verb reference](../reference/verbs.md): generated from the contracts.
- [Verdicts](verdicts.md): how facts become a verdict.
- [New verbs](../extending/verbs.md): write your own.
