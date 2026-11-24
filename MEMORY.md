# MEMORY — durable decision log

Each entry has the form:

```
## YYYY-MM-DD — decision
Why: the reason the decision was made.
How to apply: what to do (or not do) because of it.
```

Newest entries go at the bottom. The Elephant owns this file; amend the spec first,
then record the change here.

## 2026-11-24 — Dialects: native profiles plus an optional sqlglot bridge (2 + 3)
Why: native profiles give a verified path and cheap new dialects with one conformance
suite; sqlglot alone would put a translator in the core exactly where a fact checker
cannot be wrong. The bridge still covers the long tail.
How to apply: native profiles are the core and `verified`/`native` tiers; sqlglot is an
optional extra labelled `best_effort` and used to cross-check native SQL. Both start from
the same internal SQL AST so they cannot disagree on semantics.

## 2026-11-24 — Build from scratch, reuse concepts not code
Why: the owner chose a clean implementation of the ideas of an earlier internal
language, so the library carries no private code, names or data.
How to apply: never copy code from that language; use only the generic shop domain
(orders, customers, order_items, products, events, opportunities) in examples.

## 2026-11-24 — A filter is always the last element of a claim
Why: it lets attribute names such as `IS`, `IN`, `BETWEEN`, `NOT` double as filter
keywords with no ambiguity: `WHERE` starts a filter that runs to the end of the line.
How to apply: the parser never accepts an attribute after a filter; keep the grammar and
formatter consistent with this rule.

## 2026-11-24 — Paramstyle is a render-time input, not a profile field
Why: one dialect can have drivers with different parameter styles.
How to apply: probes render with neutral placeholders; `probe.bind(paramstyle)` produces
the driver form and each connector declares its own paramstyle. `compile` needs no connector.
