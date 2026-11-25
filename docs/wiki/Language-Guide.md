# Language Guide

> A claim is one line: VERB, then operands, then attributes, then an optional WHERE filter that always comes last.
> Strings use single quotes, identifiers may be quoted with double quotes or backticks, and percentages become ratios.
> This page teaches every piece of syntax with small examples; the formal grammar is in `docs/reference/grammar.md`.

## Anatomy of a claim

```
JOIN order_items.order_id -> orders.id CARD N:1 MATCH >=99% WHERE status = 'shipped'
^    ^------- operands -------------^ ^--- attributes ------^ ^----- filter ------^
verb
```

1. **Verb**: the first token, all uppercase (`JOIN`).
2. **Operands**: the things the claim is about: tables, columns, or an edge `a.x -> b.y`.
3. **Attributes**: an UPPERCASE name, an optional operator, and a value: `MATCH >=99%`, `VALUE ==1717`, `IN ['a','b']`.
4. **Filter**: `WHERE condition`, always last, running to the end of the line.

Lines starting with `#` are comments. A trailing `\` continues a claim on the next line, handy for long value lists:

```
VALUES orders.status IN ['new', 'paid', 'shipped', \
                         'cancelled', 'refunded']
```

## The one uppercase rule

**Operands end at the first all-uppercase bare word.** So an uppercase word can never be an operand; it is read as the next attribute name. If a table really is called `ORDERS` (common on Snowflake), quote it: `"ORDERS".id`.

## References (names)

A reference has one to four parts separated by dots: `[catalog.][schema.]table[.column]`. Whether a reference means a table or a column is decided by the verb, not the grammar: the last part is the column when the verb wants a column.

- Quote a name with `"double quotes"` or `` `backticks` `` when it has spaces, uppercase letters you want preserved, or clashes with a keyword.
- An unquoted name means exactly what it would mean in your warehouse's own SQL, so the warehouse applies its own case rules.
- Generating claims from code? `factlang.syntax.needs_quoting(name)` says whether a name must be quoted (it would not denote itself bare: an uppercase word, a filter keyword, a space, a leading digit), and `factlang.syntax.quote_name(name)` returns the double-quoted form (`ValueError` for an empty name or one holding CR, LF or NUL). Both are stable API.
- `EXISTS t COLUMN c` names a column through an attribute, because a bare `a.b` operand is always a table.

### The USE directive

On a warehouse with many schemas, set a default once:

```
USE analytics.sales
ROWS orders VALUE >=1000
ROWS customers VALUE >=100
```

`USE` applies to every reference in the following lines, including the right side of a `JOIN`, until the next `USE`. Directives produce no outcome.

## Values

| Kind | Examples | Notes |
|---|---|---|
| number | `42`, `-3.5`, `1_717`, `1e3` | `_` separators are removed |
| percent | `95%` | becomes the ratio `0.95` at parse time |
| duration | `30s`, `5m`, `24h`, `7d`, `2w` | becomes seconds |
| string | `'Churned'`, `'it''s'` | single quotes only; `''` is a quote |
| identifier | `integer`, `bytes` | symbolic attribute values |
| cardinality | `1:1`, `1:N`, `N:1`, `N:M` | for `JOIN CARD` |
| list | `['a', 'b']`, `[0, 100]` | trailing comma allowed |
| typed temporal | `DATE '2025-01-31'`, `TIMESTAMP '2025-01-31T12:00:00Z'` | for `RANGE`, `MIN`, `MAX` |

Operators in attributes: `>= <= == != = < >` (`=` means `==` there).

## Filters (WHERE)

```
WHERE status = 'shipped' AND (amount > 100 OR vip)
WHERE deleted IS NULL OR NOT deleted
WHERE created_at BETWEEN DATE '2025-01-01' AND DATE '2025-12-31'
WHERE country IN ['DE', 'FR'] AND discount IS NOT NULL
```

- Comparisons: `name OP literal`, `IN [...]`, `NOT IN [...]`, `IS [NOT] NULL`, `BETWEEN a AND b`.
- Combine with `AND`, `OR`, `NOT` and parentheses. `AND` binds tighter than `OR`.
- Literals: numbers, strings, `TRUE`, `FALSE`, `DATE '...'`, `TIMESTAMP '...'` (no offset means UTC).
- **Bare boolean column:** a lone name tests truth: `deleted` means `deleted = TRUE`. Under SQL's three-valued logic `NOT deleted` drops NULL rows, which is why the live-row idiom for a flag that is TRUE or NULL is `deleted IS NULL OR NOT deleted`.
- `x = NULL` is an error; use `x IS NULL`.
- A bare `TRUE` or `FALSE` as a whole condition is an error (they are keywords). Quote a column literally named `true`: `"TRUE"`.
- Filters reference columns of the claim's left table only. Not every verb accepts one; see [[Standard-Verbs]].

## Composite keys

`UNIQUE` and `FANOUT` take `WITH [more, columns]` for multi-column keys. `JOIN` takes `WITH [x2] TO [y2]` for extra left and right key columns, matched by position:

```
JOIN order_items.order_id -> orders.order_id WITH [shop_id] TO [shop_id] MATCH >=99%
```

A key tuple with any NULL part is left out. Columns are grouped, never glued together as text.

## Percentages, tolerance and exactness

`COUNT t VALUE ==1717` is exact. Add `TOLERANCE 0.5%` for a relative band or `ABS_TOLERANCE 3` for an absolute one (not both). `SUM` and `AVG` over an empty set give `no_data`, never 0.

## Canonical form

`fl.format(text)` prints the standard spelling; running it twice changes nothing, and the semantic hash stays the same. See [[Parser]].

## Mistakes you will meet

- Putting an attribute after `WHERE`: it is read as part of the filter and reported as a malformed filter.
- Double-quoting a string (`"Won"`): that is an identifier. Use single quotes.
- Forgetting to quote an uppercase table name.

All error codes are in [[Diagnostics]].

## See also

- [[Standard-Verbs]]
- [[Lexer]]
- [[Parser]]
- [[Concepts]]
- [[Diagnostics]]
