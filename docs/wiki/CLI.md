# CLI

> The `factlang` command lints, formats, explains and checks claim files from a terminal or CI job.
> It uses only the standard library's `argparse`, and its exit codes say what happened: 0 fine, 1 a claim failed, 2 invalid input, 3 errors or undecidable.
> Output is text by default; `--format json` or `--format markdown` is available before or after the subcommand.

## Commands

| Command | What it does |
|---|---|
| `factlang lint FILE...` | reports diagnostics; never executes anything |
| `factlang fmt [--check] FILE...` | rewrites files in canonical form; with `--check` only reports and exits 1 if any would change |
| `factlang explain FILE --dialect D` | prints each claim's SQL for dialect `D` (no warehouse needed) |
| `factlang check FILE --dsn URL` | checks claims on a warehouse through SQLAlchemy |
| `factlang check FILE --connector module:attr [--dialect D]` | uses a connector (or a factory, or a DB-API connection with `--dialect`) from your code |
| `factlang verbs [--json]` | lists the standard verbs |
| `factlang --version` | prints the version |

## Examples

```
factlang lint facts.fl
factlang fmt --check facts.fl
factlang explain facts.fl --dialect bigquery
factlang check facts.fl --dsn postgresql://reader@db/shop --format markdown
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | everything fine |
| 1 | a claim FAILED (or `fmt --check` found a file that would change) |
| 2 | invalid input: bad usage, unreadable file, front-end errors in a claim, unloadable connector |
| 3 | an `error` or `no_data` outcome, or a claim that cannot be rendered |

When several apply the order is 2, then 1, then 3. That makes the CLI usable as a CI gate: a non-zero code fails the job, and the code says why.

## In CI

```yaml
- run: factlang lint facts.fl && factlang fmt --check facts.fl
- run: factlang check facts.fl --dsn "$WAREHOUSE_URL"
```

## See also

- [[Language-Guide]]
- [[Diagnostics]]
- [[Connectors]]
- [[Agent-Integration]]
