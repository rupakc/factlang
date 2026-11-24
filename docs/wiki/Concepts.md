# Concepts

> Four ideas carry the whole library: a claim, a verdict, evidence, and a tier.
> A claim is one line of text; a verdict says whether the data supports it; evidence shows the proof; the tier says how much to trust the SQL path.
> Read this page first if any other page uses a word you do not know.

## Claims

A **claim** is one line stating one fact about a warehouse. It starts with a **verb** (what kind of fact), then **operands** (which tables and columns), then **attributes** (the numbers to compare against), and optionally a `WHERE` filter at the very end.

```
RANGE order_items.quantity BETWEEN [1, 100] WHERE status = 'shipped'
```

- Verb: `RANGE`
- Operand: `order_items.quantity` (a column)
- Attribute: `BETWEEN [1, 100]`
- Filter: `WHERE status = 'shipped'`

Claims live in text files or strings, one per line. A file with five mistakes reports five problems, because each broken line is skipped on its own. See [[Language-Guide]].

## Verbs and packs

The grammar itself knows no verbs. Verbs come from a **pack**, a named, versioned set of **contracts**. The built-in pack is called `standard` and holds 19 verbs such as `ROWS`, `NULLS`, `UNIQUE`, `JOIN`, `COUNT`. A **contract** is one object that describes a verb completely: its syntax, how to measure it, how to compute facts from the measurement, how to decide pass or fail, and examples. See [[Standard-Verbs]] and [[Extending-Packs]].

## Facts and predicates

A measurement returns raw numbers. A contract turns them into **facts** (for example `match_rate = 0.9774`) and then a small **predicate** compares the facts with what you claimed (`match_rate >= 0.99`). The predicate language is deliberately tiny: compare, and, or, not. See [[Judge]].

## Verdicts

Every claim ends in exactly one verdict:

| Verdict | Meaning |
|---|---|
| `pass` | Measured, and the claim is true |
| `fail` | Measured, and the claim is false |
| `no_data` | Measured, but a number the claim needs cannot be known (for example the table is empty so a rate has no denominator) |
| `error` | Could not measure: the warehouse rejected the SQL, a timeout, a cost limit |
| `unprobeable` | A claim that is semantic by design (such as `NOTE`) and cannot be tested with SQL |
| `invalid` | The text did not pass the front end; diagnostics explain why |

Two rules matter. `error` is never `no_data`: a rejected query is a defect, not an absence of data. And an undecidable fact never turns into a pass; it stays `no_data`.

## Evidence

An **outcome** is the verdict plus what backs it: the canonical claim text, a semantic hash, the facts, a plain-language explanation naming the numbers (`match_rate 0.9774 < 0.99`), and the evidence block: the SQL, its parameters, the dialect, the tier, the duration, bytes processed when the connector reports them, and the hashes of any claims that shared the query.

## Tiers

The tier tells you how well tested the SQL path is:

| Tier | Meaning | Dialects |
|---|---|---|
| `verified` | Native profile run against a real engine in CI | trino, duckdb, postgres, mysql, clickhouse, tsql |
| `native` | Native profile; SQL parsed and golden-tested in CI, not run (needs a cloud account) | bigquery, snowflake, redshift, databricks, oracle |
| `best_effort` | Produced by the optional sqlglot bridge | any other dialect sqlglot knows |

## Semantic identity

Two claims that say the same thing get the same **semantic hash**, even if they differ in comments, spacing, attribute order, `=` versus `==`, or `1_717` versus `1717`. Hosts can store the hash to track a fact over time. See [[Parser]] and [[Glossary]].

## See also

- [[Language-Guide]]
- [[Judge]]
- [[Dialects]]
- [[Glossary]]
