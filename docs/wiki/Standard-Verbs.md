# Standard Verbs

> The `standard` pack (version 1.0) ships 19 verbs in four families: structure, data quality, relationships, and answer facts, plus one semantic verb.
> Each verb is one file with at least three valid and three invalid examples that run as tests.
> Not every verb accepts a `WHERE` filter; the table below says which do.

## Structure facts (catalog only, no data scan)

| Verb | Example | Passes when | Filter |
|---|---|---|---|
| `EXISTS` | `EXISTS orders` / `EXISTS orders COLUMN shipped_at` | the table or column exists | no |
| `TYPE` | `TYPE orders.amount IS decimal` | the column's type family equals the value | no |

Type families: `integer, decimal, float, string, boolean, date, timestamp, time, bytes, json, array, struct, other`. If the catalog cannot answer, the verdict is `no_data`.

## Data-quality facts

| Verb | Example | Passes when |
|---|---|---|
| `ROWS` | `ROWS orders VALUE >=1000` | row count satisfies the comparison |
| `NULLS` | `NULLS orders.shipped_at RATE <=5%` | null rate satisfies the comparison; `no_data` if the table is empty |
| `DEAD` | `DEAD products.legacy_code` | the column is always NULL |
| `UNIQUE` | `UNIQUE orders.id` or `UNIQUE order_items.order_id WITH [line_no]` | no key is held by more than one row |
| `FANOUT` | `FANOUT order_items.order_id MAX_PER_KEY <=50` | the largest group per key satisfies the bound |
| `VALUES` | `VALUES opportunities.stage IN ['Prospect','Won','Lost','Churned']` | no undeclared values (or within `ALLOW`) |
| `RANGE` | `RANGE order_items.quantity BETWEEN [1, 100]` | min and max both inside the range |
| `PATTERN` | `PATTERN customers.email LIKE '%@%.%' RATE >=99%` | matching share of non-NULL values satisfies the comparison |
| `FRESH` | `FRESH events.created_at WITHIN 24h` | the newest value is no older than the window |

All of these accept `WHERE` except `DEAD`.

Details worth knowing:

- `null_rate = (row_count - non_null) / row_count`, and `None` for an empty table.
- `VALUES` compares the column **as text**, case-sensitive, ignoring NULLs. `SPLIT ':'` compares only the trimmed text before the separator (for "code: comment" columns). `ALLOW <=1%` tolerates a share of undeclared rows; `coverage` is by rows.
- `RANGE` takes numbers or typed temporal literals. DATE bounds over a TIMESTAMP column mean the half-open UTC interval.
- `FRESH` reads the newest value and the server's clock in one query; DATE and TIMESTAMP-without-zone are read as UTC. A non-temporal column gives `no_data`; an empty table gives `no_data`.
- `UNIQUE` reports `duplicate_keys` (keys held by more than one row) and `max_rows_per_key`; NULL keys are excluded.

## Relationship facts

`JOIN` (accepts `WHERE`, applied to the left side):

```
JOIN order_items.order_id -> orders.id CARD N:1 MATCH >=99%
JOIN order_items.order_id -> orders.order_id WITH [shop_id] TO [shop_id] MATCH >=99% COVERAGE >=50%
```

Facts: `match_rate` (matched distinct left keys over distinct non-NULL left keys), `coverage` (share of the right side reached), `row_match_rate`, `fan_in`, `distinct_left`, `distinct_right`, `matched`, `left_non_null`, `max_left_per_key`, `max_right_per_key`. It passes when `MATCH` holds, `COVERAGE` holds if given, and `CARD` agrees with the measured multiplicities (`N:1` means each right key appears at most once; `1:1` both sides; `1:N` the left side). Left NULL keys are excluded; their share is a `NULLS` claim. `CAST string` compares both sides as that type family. Any fact that cannot be decided is `None`, never 0.

## Answer facts (what an agent says in an answer)

| Verb | Example |
|---|---|
| `COUNT` | `COUNT opportunities VALUE ==1717 WHERE stage = 'Churned'` |
| `DISTINCT` | `DISTINCT orders.customer_id VALUE ==42` |
| `SUM` | `SUM orders.amount VALUE ==1234.5 TOLERANCE 1%` |
| `AVG` | `AVG orders.amount VALUE >=20` |
| `MIN`, `MAX` | `MAX orders.created_at VALUE >=DATE '2025-01-01'` |

`TOLERANCE 0.5%` is relative; `ABS_TOLERANCE 0.01` is absolute; they exclude each other; neither means exact. Exact equality uses a tight float comparison, and Decimal results are compared without a float round trip when possible. `SUM` and `AVG` over an empty set are `no_data`.

## Semantic verb

`NOTE products.price IS unit BECAUSE 'prices are in cents'` parses, gets a hash, and is reported `unprobeable` with its reason. It is the reference example of a fact that is true by definition, not by measurement; you can model your own this way.

## Not filterable

`EXISTS`, `TYPE`, `DEAD`, `NOTE` (all other verbs accept `WHERE`). A filter on them is FL308.

## Listing them

`factlang verbs` prints the table; `factlang.agent.verb_reference(pack)` returns a prompt-ready version generated from the contracts themselves.

## See also

- [[Language-Guide]]
- [[Extending-Verbs]]
- [[Judge]]
- [[Agent-Integration]]
