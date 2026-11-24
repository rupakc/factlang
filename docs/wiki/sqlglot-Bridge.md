# sqlglot Bridge

> The bridge is an optional second printer: it converts FactLang's internal SQL tree into a sqlglot expression and asks sqlglot to print it for any dialect it knows.
> It reaches the long tail (Presto, Athena, Hive, StarRocks, Doris, Teradata and more) and is always labelled `best_effort`.
> It lives in `factlang/sql/sqlglot_bridge.py`, imported lazily, and needs `pip install factlang[sqlglot]`.

## Why it exists

Native profiles are the verified path, but nobody can hand-write a profile for every warehouse. sqlglot already knows dozens. The owner chose both: native profiles as the core, sqlglot as an extra.

## Why it is not the core

A translator dependency would sit exactly where a fact checker cannot be wrong. If a translation bug turned `COUNT(DISTINCT x)` into something subtly different, a verdict would be silently wrong. So the bridge is optional, labelled best effort on every outcome, and not used when a native profile exists (unless you force it).

## How it works

1. The primitive is lowered to the internal SQL tree, the same one the native printer uses.
2. The bridge builds a sqlglot expression tree from that tree.
3. sqlglot prints it with `.sql(dialect=name)`.
4. Parameters become placeholders mapped to the target parameter style.

Because both printers share step 1, they cannot disagree on meaning.

## Cross-checking

Tests use the bridge to parse native SQL back in each dialect (the `native` tier is checked this way), and on DuckDB and Postgres they run both paths and require equal results. `Checker(..., prefer="sqlglot")` forces the bridge.

## Limits

- Tier is always `best_effort`.
- Sampling: `system` and `bernoulli` work where sqlglot has a `TABLESAMPLE` form; `keyhash` only for the dialects FactLang has a hash expression for; anything else is FL506.

## See also

- [[Dialects]]
- [[Extending-Dialects]]
- [[Design-Decisions]]
- [[Architecture]]
