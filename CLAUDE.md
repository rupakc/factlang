# FactLang — project rules

FactLang is a formal language for verifying factual claims against data warehouses.
A claim is parsed, analysed against a verb registry and optional catalog, planned
into a dialect-neutral probe IR, rendered to read-only SQL for a target dialect,
executed by a connector over all rows, and judged into a verdict with evidence.
Native dialect profiles are the verified path; an optional sqlglot bridge covers the
long tail as `best_effort`. The design lives in `docs/specs/2026-11-24-factlang-design.md`.
The front end is pure and knows no warehouse; only dialects know SQL; only connectors know a network.

## Commands

```bash
pip install -e ".[dev]"
pytest
ruff check
ruff format --check
mypy --strict src
```

## Global constraints

- Package `factlang`, `src/` layout, import name `factlang`, version `0.1.3` in `src/factlang/__about__.py`; `LANGUAGE_VERSION = "1.0"`.
- Python `>=3.10`; CI matrix 3.10, 3.11, 3.12, 3.13.
- Core (everything except `factlang/sql/sqlglot_bridge.py`, `factlang/connectors/<adapter>.py` other than `base.py`/`callable.py`/`dbapi.py`/`catalog.py`, `factlang/agent/mcp.py`) imports only the stdlib. Optional deps imported lazily inside functions.
- Layer rule spec §5.1: a module imports only from strictly lower layers.
- `mypy --strict src` clean, `ruff check` clean, `ruff format --check` clean — all blocking.
- Diagnostic codes are `FLnnn` exactly as listed in spec §12; ranges fixed.
- Metric aliases (wire format): `row_count non_null distinct_keys max_rows_per_key min_value max_value sum_value avg_value duplicate_keys server_now`.
- No input sampling, ever. No raw SQL in claims. Every literal is a typed bind parameter.
- MIT license, `Copyright (c) 2026 Rupak Chakraborty`.
- No mention anywhere of the company, product, datasets or table names of the project FactLang originated in (generic shop domain only: orders, customers, order_items, products, events, opportunities).
- Commits: conventional-commit style, each commit leaves pytest green, each commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Stage explicit paths only (never `git add -A`). Never commit `.superpowers/`.
- Use `python3`. Use a gitignored `.venv` at the repository root (create it if absent).

## Where things live (spec §5.1)

A module may import only from strictly lower layers (`tests/test_architecture.py`,
which holds `CORE_MODULES` and `LAYERS`).

| Layer | Module | Responsibility |
|---|---|---|
| 0 | `factlang/diagnostics.py` | `Span`, `Severity`, `Diagnostic`, `DiagnosticBag`, caret rendering, JSON form |
| 1 | `factlang/syntax/tokens.py` | `TokenKind`, `Token` |
| 2 | `factlang/syntax/lexer.py` | text → tokens, never raises |
| 3 | `factlang/syntax/ast.py` | generic AST: `Document`, `Directive`, `Claim`, `Ref`, `Edge`, `Literal`, `Attribute`, `Filter` nodes |
| 4 | `factlang/syntax/parser.py` | tokens → AST, line recovery |
| 4 | `factlang/syntax/formatter.py` | AST → canonical text |
| 5 | `factlang/semantics/types.py` | `ValueType` registry (built-in + custom) |
| 5 | `factlang/semantics/signature.py` | `OperandSpec`, `AttrSpec`, `VerbSpec`, `VerbRegistry` (suggestions) |
| 6 | `factlang/semantics/catalog.py` | `Catalog` protocol, `NullCatalog`, `StaticCatalog`, `CachedCatalog` |
| 6 | `factlang/semantics/checked.py` | `CheckedClaim` (resolved, typed, immutable) |
| 7 | `factlang/semantics/analyzer.py` | AST + registry + catalog → `CheckedClaim`s + diagnostics |
| 7 | `factlang/semantics/identity.py` | `semantic_hash` |
| 8 | `factlang/ir/` | primitives, metrics, filter IR, `Arity` |
| 9 | `factlang/judge/` | `Predicate` language, `Outcome`, `Verdict`, `judge()` |
| 10 | `factlang/contract.py` | `Contract`, `Example`, `ContractPack`, decorator API |
| 11 | `factlang/packs/standard/` | the standard verb pack, one file per verb |
| 12 | `factlang/sql/` | internal SQL AST, `DialectProfile`, native profiles, renderer |
| 12 | `factlang/sql/sqlglot_bridge.py` | optional; imported lazily |
| 13 | `factlang/optimizer.py` | probe fusion and de-duplication |
| 14 | `factlang/connectors/` | `Connector` protocol + optional adapters (lazy imports); `connectors/catalog.py` holds `WarehouseCatalog` (it needs a connector) |
| 15 | `factlang/engine.py` | `Checker` orchestration, sync + async, batching, limits |
| 16 | `factlang/api.py`, `factlang/__init__.py` | public facade |
| 17 | `factlang/agent/` | tool schema, verb reference, JSON I/O, optional MCP server |
| 17 | `factlang/cli.py` | `factlang` command |

Core = layers 0–13 + `connectors/base.py` + `engine.py` + `api.py` + `agent/` minus
the MCP server. Core is stdlib-only.

## Process: Elephant-Goldfish (spec §15)

- The **Elephant** (long-running session) owns the spec, the plan and `MEMORY.md`, and writes no library code.
- Each plan task goes to a fresh **goldfish** implementer that receives only the spec,
  the task and the repo; a separate fresh reviewer checks the diff against the spec.
  Independent tasks run in parallel; tasks touching the same layer run sequentially.
- If a goldfish needs information not in the spec, that is a spec defect: stop and
  report the gap; the Elephant amends the spec and records it in `MEMORY.md`.
- Commits land incrementally on `main`, each green in CI.
- To cut a task for a goldfish, use the `egm-handoff` skill.

## How to extend

- New verb: skill `add-verb` (`.claude/skills/add-verb/SKILL.md`).
- New dialect: skill `add-dialect`.
- New connector: skill `add-connector`.
- Conformance suite: skill `run-conformance`. Releases: skill `release`.
- Roles live in `.claude/agents/`; durable decisions in `MEMORY.md`.
