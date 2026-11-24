# Judge

> The judge takes the rows a probe returned and produces an `Outcome`: it derives facts, decides with a small predicate, and attaches the evidence.
> It never raises, so one broken contract cannot sink a whole batch; anything unexpected becomes an `error` outcome.
> It is split into derive (compute facts, may say "unknown") and decide (a plain comparison), with three-valued logic in between.

## Derive and decide

- **derive** turns the raw measurement into named facts. For a fact the data cannot support (a rate over zero rows) it returns `None`, never 0.
- **decide** is a predicate over those facts, built from `Compare`, `And`, `Or`, `Not`, `Within`, and claim-dependent forms such as `WhenClaimHas` and `CompareUsingClaimOp`.

Why the split: a `None` that was quietly coerced to `0` would satisfy `<= 1` and pass while proving nothing. Here an undecidable fact propagates, and the verdict is `no_data`.

## Three-valued logic

A predicate evaluates to true, false or unknown. `And`, `Or`, `Not` follow the usual three-valued rules, and unknown at the top becomes `no_data`. An unknown can never be turned into a pass.

## A deliberately weak language

Predicates can compare, combine and negate, nothing more. That keeps them serialisable (they print as JSON) and explainable (the explanation names the numbers: `match_rate 0.9774 < 0.99`). Anything harder is derivation, written as ordinary Python in the contract.

## Outcome

`Outcome` is a frozen dataclass with `to_dict()`: claim text, canonical text, semantic hash, verb, verdict, explanation, facts, evidence (SQL, params, dialect, tier, duration, bytes processed, fused-with hashes) and diagnostics. `Report` groups outcomes with summary counts, an `ok` flag, and `to_dict()`, `to_json()`, `to_markdown()`.

## Verdict mapping

| Situation | Verdict |
|---|---|
| predicate true | `pass` |
| predicate false | `fail` |
| a needed fact is `None`, or no rows | `no_data` |
| execution error, timeout, cost limit, render failure | `error` |
| contract with a reason it cannot be probed | `unprobeable` |
| front-end errors | `invalid` |

## Payloads

`judge.run.payload_for` shapes the result rows from the plan's declared arity: a single-row plan gives the row dict, a row-set plan gives `{"rows": [...]}`, no rows gives `None`. The runner never assumes `rows[0]`.

## See also

- [[Concepts]]
- [[IR]]
- [[Extending-Verbs]]
- [[Measurement-API]]
