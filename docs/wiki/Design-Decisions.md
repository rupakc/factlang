# Design Decisions

> FactLang carries forward 14 ideas from an earlier internal language; each one exists because its absence once caused a measured wrong answer.
> This page explains each idea and its reason, then the decisions made for FactLang itself, including the two amendments that added measurement features for tools built on top of it.
> The full text lives in `docs/specs/2026-11-24-factlang-design.md`, and dated decisions are logged in `MEMORY.md`.

FactLang is a from-scratch reimplementation: the ideas were kept, none of the code was copied, and no private names or data appear anywhere.

## The 14 concepts

**1. Line-oriented claims.** One claim per line. *Reason:* a file with five mistakes should report five problems, not stop at the first. Error recovery just skips to the next line. See [[Parser]].

**2. A verb-agnostic grammar.** The parser knows no verbs; verbs are checked against a registry afterwards. *Reason:* adding a verb should be data, never a parser change, so extension is cheap and cannot break parsing. See [[Parser]], [[Extending-Verbs]].

**3. A claim kind is one object.** A `Contract` bundles syntax, plan, derivation, judgement and examples, registered into a named, versioned pack. *Reason:* when pieces of a verb live in different places they drift apart; one object keeps them consistent and testable. See [[Extending-Verbs]], [[Extending-Packs]].

**4. A small dialect-free IR with declared arity.** Measurement primitives say how many rows they return and what the columns are called. *Reason:* code that assumed "the first row" broke whenever a query returned a set; shaping results from the plan removes the guess. See [[IR]].

**5. No sampling, ever, in verdicts.** *Reason:* `LIMIT` returns rows in storage order, not at random, and sampled probes reported 22.5% where the exact answer was 6.8%. A fact checker that approximates is not a fact checker. (Amendment B later allowed sampled *measurement* with strict rules; the verdict rule stands.) See [[Measurement-API]].

**6. NULL semantics declared per metric, not per dialect.** For example max-rows-per-key drops NULL keys before grouping, and composite keys are grouped, never concatenated. *Reason:* if each dialect decided, the same claim would give different answers on different engines. See [[IR]].

**7. Metric column aliases are a wire format.** The names `row_count`, `non_null`, `distinct_keys` and the rest are pinned by a conformance test. *Reason:* downstream code reads those columns by name; a silent rename breaks it without any error. See [[IR]].

**8. A derive and decide split with three-valued logic.** `derive` returns `None` for a fact the data cannot support, `decide` is a plain comparison, and "undecidable" propagates. *Reason:* a `None` coerced to `0` satisfies `<= 1` and passes while proving nothing. See [[Judge]].

**9. A deliberately weak predicate language.** Compare, and, or, not. *Reason:* it must be serialisable and explainable; anything harder belongs in derivation, where it is ordinary, testable code. See [[Judge]].

**10. Judging never raises.** *Reason:* one broken contract must not sink a batch of a hundred claims. Failures become `error` outcomes. See [[Judge]].

**11. Diagnostics with stable codes, spans, carets and suggestions.** *Reason:* people fix errors faster with a caret and a "did you mean", and tools and agents need codes that never change meaning. See [[Diagnostics]].

**12. Percentages normalise at parse time, and a semantic identity hash ignores spelling.** `95%` is `0.95`; comments, whitespace, attribute order and `1_717` versus `1717` do not change identity. *Reason:* the same fact written two ways must be recognised as the same, for de-duplication and history. See [[Parser]].

**13. Executable documentation.** The examples in the grammar document and in every contract are run as tests. *Reason:* documentation that cannot fail goes stale; documentation that is tested cannot. See [[Contributing]].

**14. Layering enforced by test, and a portability test on the core's imports.** *Reason:* architecture rules that live only in people's heads erode. A test that fails on a bad import keeps the core small and dependency-free. See [[Architecture]].

## Decisions made for FactLang

**Native profiles plus an optional sqlglot bridge.** Three approaches were weighed: hand-written SQL per warehouse (precise but ten renderers drift on NULL and case rules), one shared generator with small profiles, and sqlglot transpilation (widest reach, but a translator dependency sits exactly where a checker cannot be wrong). The owner chose the shared generator plus the bridge: native profiles are the core and the verified path; sqlglot is an extra labelled `best_effort`, used also to cross-check native SQL. Both start from one SQL tree, so they cannot disagree on meaning. See [[Dialects]], [[sqlglot-Bridge]].

**A filter is always the last element of a claim.** *Reason:* it lets attribute names such as `IS`, `IN`, `BETWEEN`, `NOT` double as filter keywords with no ambiguity.

**Paramstyle is a render-time input.** *Reason:* one dialect can have drivers with different parameter styles, so the SQL template uses neutral placeholders and `probe.bind(paramstyle)` produces the driver form. `compile` therefore needs no connector.

**An identifier means what it would mean in your warehouse.** Unquoted names stay unquoted so the warehouse applies its own case rules; FactLang never second-guesses them.

**No raw SQL in a claim; every literal is a bind parameter.** *Reason:* it closes injection by construction and keeps claims explainable.

**Unknown is not an error.** A catalog that cannot answer produces an info note, not a false failure. See [[Catalogs]].

**Standard library only in the core.** *Reason:* any project can install it with zero extra dependencies; heavy things are extras.

## Amendment A: capabilities for tools built on FactLang

A schema-intelligence library (SchemaGuru) depends on FactLang for all measurement SQL, dialects, connectors and verdicts. A cold review of its needs found ten gaps, and the owner directed that they ship in 0.1.0 so that library never renders SQL itself or relies on internals:

1. **Raw measurement** without a claim (`Checker.measure`, `fl.measure`).
2. **Top-k observed values** by allowing an empty declared list in `ValueMembership`.
3. **Composite join keys** (`WITH [...] TO [...]`).
4. **Reverse and row join facts** (`coverage`, `row_match_rate`, `fan_in`, and more).
5. **Cost estimate without executing** (`Checker.estimate`).
6. **Public filter and join rendering** (`fl.parse_filter`, `fl.sql.render_filter`, `render_join`, `RenderedFragment`).
7. **Native Trino** profile at the `verified` tier.
8. **Guarded metadata SQL** through any connector (`fl.run_metadata`, FL703).
9. **A bare boolean filter atom** (`WHERE deleted`).
10. **An API stability promise** for all of the above.

See [[Measurement-API]].

## Amendment B: sampled measurement, never sampled verdicts

For terabyte tables the owner ruled that measurement may use a representative sample while small tables stay exact. The original no-sampling rule protected *verdicts*, so the amendment keeps that and adds a narrow door: a `SampleSpec` (`keyhash`, `system` or `bernoulli`, with a seed) accepted only by `Checker.measure`. Claims, contracts and the analyzer never sample (FL404). A dialect that cannot express a method raises FL506 and never falls back to exact or to `LIMIT`. Multi-read plans cannot use block or row sampling (FL405) because such samples may differ between reads. `keyhash` is deterministic, and the same seed on both join sides keeps the same keys. Results are raw sample metrics plus `sampled_rows`; scaling and estimating are the host's job. See [[Measurement-API]].

## See also

- [[Concepts]]
- [[Architecture]]
- [[Measurement-API]]
- [[Contributing]]
