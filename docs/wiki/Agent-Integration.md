# Agent Integration

> FactLang gives an LLM agent three things: tool definitions it can call, a prompt-ready reference of the verbs, and JSON results it can read.
> An agent drafts claims about its own answer, FactLang verifies them against the warehouse, and the agent corrects itself before replying.
> Everything is generated from the active pack, so the documentation an agent sees cannot drift from what the library accepts.

## The loop

1. The agent answers a question and writes the facts behind the answer as claims.
2. It calls `factlang_lint` to fix syntax mistakes, then `factlang_check` to verify.
3. Each outcome comes back with a verdict, the numbers and the SQL.
4. On `fail` or `no_data` the agent revises its answer instead of stating an unchecked fact.

## Tool definitions

```python
from factlang.agent import tool_definitions, handle_tool_call

tools = tool_definitions()                 # JSON-schema specs for any function-calling API
result = handle_tool_call("factlang_check", {"claims": "ROWS orders VALUE >=1"}, checker)
```

| Tool | Input | What it does |
|---|---|---|
| `factlang_check` | `claims` | verifies claims on the warehouse; returns outcomes |
| `factlang_lint` | `claims` | syntax and name diagnostics only; runs nothing |
| `factlang_explain` | `claims`, `dialect` | returns the SQL that would run, with parameters |

The `factlang_check` description includes a compact verb reference, so the model learns the language from the tool itself. `handle_tool_call` never raises: bad arguments come back as a structured error.

## Verb reference for prompts

```python
from factlang.agent import verb_reference
print(verb_reference(style="markdown"))   # or style="compact"
```

It is built from each contract's spec and examples. Use a restricted pack ([[Extending-Packs]]) to limit what the agent can say.

## JSON everywhere

`Outcome.to_dict()`, `Report.to_json()` and diagnostics' JSON form carry every field, so an agent or a log pipeline can consume them directly. Diagnostics include codes, spans and did-you-mean suggestions ([[Diagnostics]]).

## MCP server

With the extra `factlang[mcp]`:

```
factlang mcp [--dsn URL | --dialect D] [--timeout S] [--max-bytes N] [--name N]
```

serves the same three tools over stdio. Give `--dsn` or `--dialect`; with neither the command exits 2. With `--dialect D` and no `--dsn`, `factlang_lint` and `factlang_explain` work and `factlang_check` reports that there is no connector. Without the extra it prints an install hint. `python -m factlang.agent.mcp` is the same server. Note that `--dsn` puts the password in the process list; only `factlang check` reads `FACTLANG_DSN` (see [[CLI]]), so give the MCP server a low-privilege read-only account.

## Safety for agents

- A claim has no raw SQL, and all literals are bound parameters, so a model cannot inject SQL through a claim.
- The connection's credentials are the real boundary: give the agent a read-only account.
- Use `max_bytes` on pay-per-scan warehouses.

## See also

- [[Standard-Verbs]]
- [[Extending-Packs]]
- [[CLI]]
- [[Diagnostics]]
