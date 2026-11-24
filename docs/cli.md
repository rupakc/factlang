# Command line

Installing FactLang installs the `factlang` command (also `python -m factlang`). It uses
only the standard library; `check` needs a way to reach your warehouse.

```text
factlang lint FILE...                      report diagnostics; never executes
factlang fmt [--check] FILE...             rewrite files in canonical form
factlang explain --dialect D FILE          print each claim's SQL
factlang check [--dsn URL | --connector MODULE:ATTR] [--dialect D] FILE
factlang verbs [--json]                    list the verbs of the standard pack
factlang mcp [--dsn URL | --dialect D]     serve the tools over MCP on stdio
```

`--format text|json|markdown` chooses the output and may come before or after the
subcommand. Any `FILE` may be `-` to read standard input; `fmt -` prints the canonical form
instead of rewriting a file.

## lint

```bash
factlang lint contracts/*.fl
cat facts.fl | factlang lint --format json -
```

Parses and analyses every file with the standard pack and prints caret diagnostics. Nothing
is executed and no warehouse is needed, so it is the right check for a pre-commit hook.

## fmt

```bash
factlang fmt facts.fl            # rewrite in place
factlang fmt --check facts.fl    # exit 1 if the file would change (for CI)
```

## explain

```bash
factlang explain --dialect bigquery facts.fl
```

```sql
-- UNIQUE orders.id
SELECT a.non_null AS non_null, ... FROM orders ...;

-- ROWS orders VALUE>=1
SELECT COUNT(*) AS row_count FROM orders;
```

The SQL is the display form, literals inlined. Any native dialect works, and with
`factlang[sqlglot]` installed any dialect sqlglot knows.

## check

```bash
export FACTLANG_DSN="postgresql+psycopg://reader@warehouse/shop"
factlang check facts.fl
factlang check --format markdown facts.fl > report.md
factlang check --connector myproject.warehouse:connector facts.fl
```

The warehouse comes from one of:

- `--dsn URL`: a SQLAlchemy URL (needs `factlang[sqlalchemy]` and the driver);
- `FACTLANG_DSN`: the same URL from the environment, used when neither `--dsn` nor
  `--connector` is given;
- `--connector module:attr`: a `Connector`, a factory returning one, or a DB-API connection
  (then give `--dialect`). This imports and runs `module` as ordinary Python code, with the
  current working directory added to `sys.path`, so only name a module you trust and run the
  command from a directory you trust.

!!! warning "Keep credentials off the command line"
    A URL with a password passed as `--dsn` is visible to other users of the machine in the
    process list. Put it in `FACTLANG_DSN`, or use `--connector` with code that reads your
    secret store.

The last line of the text output sums the report up and agrees with the exit code:
`verified: 6 PASS` (exit `0`), `ok, nothing verified: no claims` (exit `0`, nothing was
measurable: an empty file or only `unprobeable` claims) or `not verified: 1 NO_DATA` (any
non-zero exit). The JSON and Markdown reports carry `verified` and `ok`; see
[Verdicts](guide/verdicts.md#reports).

## verbs

```bash
factlang verbs           # one line per verb
factlang verbs --json    # machine-readable signatures
```

## mcp

Serves `factlang_check`, `factlang_lint` and `factlang_explain` over MCP on stdio; needs
`factlang[mcp]`. The warehouse URL comes from `--dsn` or, when neither `--dsn` nor
`--dialect` is given, from `FACTLANG_DSN`; with none of the three it exits `2`. See [Agents and LLMs](agents.md#mcp-server).

## Exit codes

| code | meaning |
|---|---|
| `0` | every claim passed (or is unprobeable) |
| `1` | a claim failed; for `fmt --check`, a file would be reformatted |
| `2` | invalid input: bad usage, an unreadable file, a claim or directive with front-end errors, a connector that cannot be loaded |
| `3` | errors or undecidable: an `error` or `no_data` outcome, a claim that cannot be rendered |

When several apply, `2` wins over `1`, and `1` over `3`. In CI, `factlang check facts.fl`
alone fails the job on any failed or unmeasurable claim.

## See also

- [Verdicts](guide/verdicts.md)
- [Agents and LLMs](agents.md)
