# Verdicts

Every claim ends in exactly one of six verdicts:

| verdict | meaning |
|---|---|
| <span class="fl-verdict fl-verdict--pass">pass</span> | measured, and the claim holds |
| <span class="fl-verdict fl-verdict--fail">fail</span> | measured, and the claim does not hold |
| <span class="fl-verdict fl-verdict--no_data">no_data</span> | measured, but a fact the claim needs is undecidable (for example an empty table) |
| <span class="fl-verdict fl-verdict--error">error</span> | could not measure: the warehouse rejected the query, a timeout, the cost budget, a rendering error |
| <span class="fl-verdict fl-verdict--unprobeable">unprobeable</span> | a semantic fact by design (`NOTE`); recorded, never measured |
| <span class="fl-verdict fl-verdict--invalid">invalid</span> | did not get through the front end; the diagnostics say why |

Two distinctions carry most of the weight:

**`no_data` is not `pass`.** A verb's `derive` step returns `None` for a fact the data
cannot support: the NULL rate of an empty table, the sum of no rows, a join with no keys on
one side. A predicate over `None` is undecidable, and undecidable propagates through `and`,
`or` and `not` instead of collapsing to a pass. Otherwise a missing number coerced to 0
would satisfy `<= 1` and pass while proving nothing.

**`error` is not `no_data`.** A warehouse that rejects the SQL is a defect to fix, not an
absence of data. Execution problems carry an `FL7xx` code (`FL700` failed, `FL701` over the
cost budget, `FL702` timeout).

## How a verdict is decided

A contract measures, derives facts, then decides with a small predicate language:
comparisons (`Compare`, `CompareUsingClaimOp`, `Within` for tolerances) combined with `And`,
`Or`, `Not` and `WhenClaimHas` (apply a check only when the claim has an attribute). The
language is deliberately weak: it serialises, and it explains itself. The `explanation` of
every outcome names the numbers, such as `match_rate 0.9765625 < 0.99`.

Judging never raises. A contract that throws while deriving or deciding produces an `error`
outcome for its own claim; one broken contract cannot sink a batch.

## The outcome object

`Outcome` is a frozen dataclass; `to_dict()` gives the JSON form.

| field | |
|---|---|
| `claim_text` | the claim as written |
| `canonical_text` | its canonical form (see [`fl.format`](language.md#canonical-form-and-identity)) |
| `semantic_hash` | `sha256:…`, identical for the same fact however it is spelled |
| `verb` | the verb |
| `verdict` | a `Verdict` (a `str` enum: `"pass"`, `"fail"`, …) |
| `explanation` | one line naming the numbers |
| `facts` | every fact the verb derived, e.g. `match_rate`, `coverage`, `top_undeclared` |
| `evidence` | the SQL, parameters, dialect, tier and timings (see [Evidence](evidence.md)) |
| `diagnostics` | the claim's diagnostics as dictionaries |

## Reports

`Checker.check(text)` returns a `Report`: the ordered outcomes, every diagnostic raised while
checking, and

- `report.verified`: every claim that can be measured was measured and passed. It is `True`
  only when there is no `fail`, `error`, `invalid` or `no_data` outcome, no error diagnostic,
  and at least one outcome that is not `unprobeable`; an empty report is not verified. Use
  it when "the facts hold" is what you need to know.
- `report.ok`: no `fail`, `error` or `invalid` outcome and no error diagnostic (a broken `USE`
  line, which has no outcome of its own, still makes the report not ok). It is the weaker
  flag: `no_data` and `unprobeable` outcomes, and an empty report, are still ok, so `ok`
  means "nothing was contradicted", not "everything was confirmed".
- `report.summary()`: a count per verdict.
- `report.to_dict()`, `report.to_json()`, `report.to_markdown()`; each carries both flags, and
  `verified` is the first key of the dict.

`fl.check(text, connector=…)` is the one-call form. It returns the `Outcome` when the text
holds exactly one claim and the `Report` otherwise, so code that checks a document should use
`Checker.check`, which always returns a `Report` with its document-level diagnostics.

## The command line

`factlang check` turns verdicts into exit codes: `0` everything passed, `1` a claim failed,
`2` invalid input, `3` errors or undecidable claims. Its last line says `verified`, `ok,
nothing verified` (exit `0` with nothing measurable) or `not verified` (any non-zero exit),
followed by the counts. See [Command line](../cli.md).

## See also

- [Evidence](evidence.md)
- [Diagnostics reference](../reference/diagnostics.md)
