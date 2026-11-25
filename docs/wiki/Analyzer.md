# Analyzer

> The analyzer takes the generic tree and checks it against a pack of verbs and, optionally, a catalog of real table and column names.
> The result is a `CheckedClaim`: resolved, typed and immutable, or a diagnostic explaining what is wrong.
> It is pure (no network) and never raises; it lives in `factlang/semantics/analyzer.py` (layer 7).

## What it checks

For each claim, in order:

1. **Verb** exists in the active pack (FL300, with a did-you-mean suggestion).
2. **Operands**: count (FL301) and kind and reference depth (FL302). A column reference with more than four parts is FL302.
3. **Attributes**: known (FL303), required ones present (FL304), value type fits (FL305), operator allowed (FL306), values in range such as a ratio above 1 (FL309), mutually exclusive pairs such as `TOLERANCE` and `ABS_TOLERANCE` (FL309). A `TOLERANCE` or `ABS_TOLERANCE` on a `MIN`/`MAX` whose `VALUE` is a date or timestamp gives a warning (FL314): the band has no effect and the claim will be ERROR when judged. The rule is declared on the verb (`VerbSpec.no_band_on_temporal`), not written into the analyzer.
4. **Filter**: allowed for this verb (FL308), literals valid (FL309 for a bad date), the filter column exists and has a sensible type (FL305 for a bare boolean atom over a non-boolean column).
5. **Names**: table and column exist in the catalog (FL311). A join across different types without `CAST` gives a warning (FL312).

`USE` namespaces are expanded here so every reference is fully resolved.

## Unknown is not an error

When the catalog cannot answer (`NullCatalog`, or the warehouse lookup is unavailable), the analyzer accepts the claim and adds an info diagnostic **FL313** "could not verify names". FL311 is raised only on a definite "no such column". This is what lets the analyzer work with no database at all.

## Value types

Attribute values are checked against a registry of value types (`fl.types`): numbers, ratios, durations, strings, lists, temporal literals and so on. You can register your own, see [[Extending-Verbs]].

## Verb specs

A verb's shape is described by a `VerbSpec`: operand specs, attribute specs, whether it is filterable, and `requires` / `exclusive` rules. The same spec drives the analyzer, the prompt-ready verb reference for agents ([[Agent-Integration]]) and the docs, so they cannot drift apart.

## Output

A `CheckedClaim` holds the verb, resolved table and column references, typed attribute values, the filter tree, and the semantic hash. Because it is immutable it is safe to share across threads in a batch.

## See also

- [[Parser]]
- [[Catalogs]]
- [[Standard-Verbs]]
- [[Diagnostics]]
- [[IR]]
