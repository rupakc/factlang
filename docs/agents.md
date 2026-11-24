# Agents and LLMs

FactLang was designed for the claims AI agents make. An agent that answers questions over a
warehouse can write the numbers in its answer as FactLang, check them, and only state what
held. Everything an agent needs is generated from the active verb pack, so it cannot drift
from what the analyser accepts.

## Tool definitions

```python
import factlang as fl
from factlang.agent import tool_definitions, handle_tool_call

tools = tool_definitions()          # three JSON-schema tool specs
print([t["name"] for t in tools])   # ['factlang_check', 'factlang_lint', 'factlang_explain']
```

| tool | arguments | does |
|---|---|---|
| `factlang_check` | `claims` | measures every claim and returns one outcome per claim, with facts and SQL evidence |
| `factlang_lint` | `claims` | validates without touching the warehouse: diagnostics with codes, positions, hints and suggestions |
| `factlang_explain` | `claims`, `dialect` | the SQL each claim would run, without executing anything |

Each spec is `{"name", "description", "input_schema"}`. The `factlang_check` description
embeds the compact verb reference, so the model sees the verbs, their attributes and two
examples each. Adapt the dict to your LLM API's tool format (for example rename
`input_schema` to `parameters`).

## Dispatching tool calls

`handle_tool_call(name, arguments, checker)` runs one call and returns a JSON-safe dict. It
never raises: bad arguments, an unknown tool or an internal failure come back as
`{"error": {"code", "message", "tool"}}`.

```python
checker = fl.Checker(dialect="postgres")      # no warehouse: lint and explain still work
result = handle_tool_call("factlang_lint", {"claims": "UNIQE orders.id"}, checker)
print(result["diagnostics"][0]["suggestion"])  # UNIQUE
```

A typical loop: the model drafts claims, calls `factlang_lint` until they are clean, then
`factlang_check`; your host keeps the sentences whose claims passed and rewrites or drops the
rest. The `factlang_check` result is `Report.to_dict()`, and its first key is `verified`:
`true` only when every measurable claim was measured and passed. `ok` is weaker (it is still
`true` with `no_data` outcomes or nothing measured), so gate on `verified`. Outcomes carry a semantic hash, so a host can cache verdicts per fact.

## The verb reference for prompts

```python
from factlang.agent import verb_reference

system_prompt = "You can verify facts with FactLang.\n\n" + verb_reference(style="compact")
```

`style="markdown"` is the full reference (the [verb reference](reference/verbs.md) page is
built from the same contracts); `style="compact"` is a few lines per verb. Both take a
`pack` argument, so custom verbs appear automatically.

## MCP server

With the extra `factlang[mcp]`, the same three tools are served over MCP on stdio:

```bash
pip install "factlang[mcp,sqlalchemy]"
export FACTLANG_DSN="postgresql+psycopg://reader@warehouse/shop"
factlang mcp                          # the URL comes from FACTLANG_DSN
factlang mcp --dialect snowflake      # no warehouse: lint and explain only
```

Options: `--timeout` (per-probe seconds, default 60), `--max-bytes` (refuse probes priced
above this by a dry run), `--name` (the server name). Without a DSN, `factlang_check`
answers with a `no_connector` error. `python -m factlang.agent.mcp` is the same server.

Register it with your MCP client as a stdio command, e.g. `factlang mcp` with
`FACTLANG_DSN` in the server's environment. Use a
database role that can only read: FactLang's probes are `SELECT`-only, and the credentials
are what guarantee the rest.

!!! warning "Credentials on the command line"
    A DSN with a password on the command line is visible to other users of the machine in
    the process list. `factlang check` and `factlang mcp` both read `FACTLANG_DSN` from the
    environment when neither `--dsn` nor (`check`) `--connector` / (`mcp`) `--dialect` is
    given; prefer that.

## Grounding evaluations

For LLM evaluation, score an answer by whether its facts hold: have the model emit claims
alongside its answer, check them, and count `pass` against `fail`. Treat `no_data`, `error`
and `invalid` separately: they mean "not established", not "wrong".

## See also

- [Getting started](getting-started.md)
- [Command line](cli.md)
- [Diagnostics](reference/diagnostics.md): what the codes in lint results mean.
