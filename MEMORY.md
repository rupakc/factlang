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

## 2026-11-24 — Amendment A: downstream requirements from SchemaGuru ship in 0.1.0 (owner-directed, URGENT)
Why: SchemaGuru (rupakc/schemaguru) depends on FactLang for all measurement SQL, dialects,
connectors and verdicts. A cold builder check found ten capabilities FactLang v1 lacked
(raw primitive measurement, empty-declared top-k, composite join keys, reverse/row join
facts, cost estimate without executing, public filter/join rendering, native Trino,
guarded metadata SQL, bare boolean filter atom, a stability promise). The owner directed
that they be added to FactLang 0.1.0 with immediate effect, so SchemaGuru never renders
SQL itself or leans on FactLang internals.
How to apply: spec §18 is the index and acceptance list (R1–R10); each item is specified
inline in the section it names. Plan: Task 26 (parser/formatter bare boolean atom) runs
NOW in parallel with Task 5; every not-yet-started task carries an "Amendment A" note that
is part of its scope; Task 27 is the acceptance sweep before release. New code FL703.

## 2026-11-24 — Amendment B: sampled *measurement* (never sampled verdicts) for very large tables
Why: the owner ruled that SchemaGuru must measure terabyte tables on a representative
sample; SchemaGuru renders no SQL, so FactLang must provide it. The original "no sampling"
rule protected verdicts — a LIMIT "sample" once overstated a rate 3.3×.
How to apply: spec §19. `SampleSpec` (keyhash / system / bernoulli, seeded) only via
`Checker.measure`; claims/contracts never sample (FL404); a dialect that cannot express a
method raises FL506, never falls back to exact or LIMIT. Payloads are raw sample metrics +
`sampled_rows`; the host estimates. Same-seed keyhash on both join sides keeps the same keys.

## 2026-11-25 — inline output is a compatibility surface
Any change to inlined literal output (spellings, prefixes such as `N'…'`, boolean/timestamp forms, refusal codes such as FL507) gets its own CHANGELOG entry, even in a patch release.
**Why:** a downstream consumer re-checks every rendered literal and fails closed on unrecognised forms.
**How to apply:** when touching `sql/probe.py` inlining or any profile's literal fields, add a "Changed (inline output)" bullet to CHANGELOG `[Unreleased]`.
