# Filters

Every measurable verb accepts a `WHERE` filter that restricts the rows it measures. The
filter is always the **last** element of a claim and runs to the end of the line.

```factlang
COUNT orders VALUE ==1400 WHERE status = 'paid'
NULLS orders.shipped_at RATE <=1% WHERE status IN ['paid', 'refunded']
ROWS events VALUE >=1 WHERE created_at >= TIMESTAMP '2026-11-01T00:00:00Z'
UNIQUE customers.email WHERE deleted IS NULL OR NOT deleted
```

Because the filter always comes last, attribute names such as `IN`, `IS`, `BETWEEN` and
`NOT` can double as filter keywords with no ambiguity. Write attributes first:
`COUNT orders VALUE ==1400 WHERE status = 'paid'`, never
`COUNT orders WHERE status = 'paid' VALUE ==1400` (that is `FL206`: the filter would have to
swallow `VALUE`).

## The condition language

```text
condition  = conj { OR conj }
conj       = atom { AND atom }
atom       = NOT atom | ( condition ) | comparison | name
comparison = name OP literal
           | name IN [ ... ]          | name NOT IN [ ... ]
           | name IS NULL             | name IS NOT NULL
           | name BETWEEN literal AND literal
literal    = number | 'string' | TRUE | FALSE | DATE '…' | TIMESTAMP '…'
```

- Precedence from loosest to tightest: `OR`, `AND`, `NOT`. Use parentheses when in doubt.
- `OP` is one of `= == != < <= > >=`.
- The left side of a comparison is a single column of the claim's (left) table, never a
  dotted ref and never an expression.
- `x IS NULL` is the only test for NULL. `x = NULL` is `FL206`, because in SQL it is never
  true.
- Filter keywords are recognised in uppercase only. A column whose name is a keyword must be
  quoted: `WHERE "status" = 'x'` is fine, and so is `WHERE "NULL" = 1`.

### Bare boolean columns

A column name standing alone tests a boolean column for truth: `WHERE deleted` means
`WHERE deleted = TRUE`, and `NOT deleted` means `NOT (deleted = TRUE)`. Under SQL's
three-valued logic `NOT deleted` excludes rows where `deleted` is NULL, so the idiomatic
live-row filter for a flag that is TRUE or NULL is:

```factlang
ROWS orders VALUE >=1 WHERE deleted IS NULL OR NOT deleted
```

When a catalog knows the column is not boolean, the analyser reports `FL305`. A bare `TRUE`
or `FALSE` atom is `FL206`; they are keywords, not names.

## Literals are typed bind parameters

Every literal in a filter becomes a typed bind parameter, named `p1, p2, …` in source order:
a number becomes an integer or decimal parameter, a string a string, `TRUE`/`FALSE` a
boolean, `DATE '…'` a date and `TIMESTAMP '…'` a timestamp (no offset means UTC). An invalid
ISO date such as `DATE '2026-02-30'` is `FL309`. No literal is ever spliced into SQL text,
so this filter is harmless:

```factlang
ROWS orders VALUE ==0 WHERE status = 'x''; DROP TABLE orders; --'
```

## Which verbs accept a filter

Filterable: `ROWS NULLS UNIQUE FANOUT VALUES RANGE PATTERN FRESH JOIN COUNT DISTINCT SUM AVG
MIN MAX`. Not filterable: `EXISTS TYPE DEAD NOTE` (`FL308`). On a `JOIN`, the filter applies
to the left table.

A filter is part of a claim's identity: `ROWS orders VALUE >=1` and the same claim with
`WHERE status = 'paid'` are different facts with different semantic hashes. Claims over the
same table with the same filter share one scan (see [Batching](batching.md)).

## Filters without a claim

Libraries that need FactLang's filter language for their own SQL can parse a bare condition
and render it for any dialect; see [Filter and join fragments](../extending/fragments.md).

## See also

- [Grammar reference, section 4](../reference/grammar.md#4-filters)
- [The language](language.md)
