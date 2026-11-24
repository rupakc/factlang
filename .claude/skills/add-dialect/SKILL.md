---
name: add-dialect
description: Add a native SQL dialect profile to FactLang. Use when asked to support a new warehouse dialect.
---

# Add a dialect

1. Read spec section 8 (dialects, tiers, bind parameters).
2. Add a `DialectProfile` under `src/factlang/sql/` (identifier quoting, identifier length limit, type names, function names, limit syntax, boolean/NULL handling). Paramstyle is NOT a profile field.
3. Register the profile by name (and aliases) in the profile registry.
4. Every literal renders as a bind placeholder; check that identifiers with NUL/newline or over-length raise FL502.
5. Add the dialect to the conformance matrix: every primitive x every metric must render; assert the metric aliases exactly.
6. If a real engine is reachable (service container or embedded), add an `integration`-marked execution test and mark the tier `verified`; otherwise the tier is `native`.
7. Cross-check the rendered SQL against the sqlglot bridge in tests (the bridge is optional and lazy).
8. Run `pytest tests/conformance`, then the full checks; update `docs/reference` dialect table and `CHANGELOG.md`.
