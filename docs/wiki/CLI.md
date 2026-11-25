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
| `factlang check FILE` (no `--dsn`) | reads the URL from the `FACTLANG_DSN` environment variable |
| `factlang verbs [--json]` | lists the standard verbs |
| `factlang mcp [--dsn URL \| --dialect D] [--timeout S] [--max-bytes N] [--max-claims N] [--name N]` | serves the FactLang tools over MCP on stdio (needs `factlang[mcp]`); without `--dsn` or `--dialect` it reads `FACTLANG_DSN`, and exits 2 if that is unset too; `--max-claims` (default 200) refuses `factlang_check` / `factlang_explain` calls with more claims, error code `too_many_claims` |
| `factlang --version` | prints the version |

## Standard input and credentials

- A `FILE` of `-` means standard input (`factlang lint -`, `factlang explain - --dialect duckdb`); with `fmt` it prints the canonical form.
- `check` uses `FACTLANG_DSN` when neither `--dsn` nor `--connector` is given; `mcp` uses it when neither `--dsn` nor `--dialect` is given. Prefer it: `--dsn` puts the password in the process list, the environment variable does not.

## Examples

```
factlang lint facts.fl
factlang fmt --check facts.fl
factlang explain facts.fl --dialect bigquery
echo "UNIQUE orders.id" | factlang lint -
FACTLANG_DSN=postgresql://reader@db/shop factlang check facts.fl --format markdown
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
- run: factlang check facts.fl
  env:
    FACTLANG_DSN: ${{ secrets.WAREHOUSE_URL }}
```

## See also

- [[Language-Guide]]
- [[Diagnostics]]
- [[Connectors]]
- [[Agent-Integration]]
