# AGENTS.md — rules for any coding agent

These rules apply to every agent working in this repository, Claude or not. The
full text is in `CLAUDE.md`; the design is in `docs/specs/2026-11-24-factlang-design.md`.

## Rules

- Package `factlang`, `src/` layout, import name `factlang`, version `0.1.2` in `src/factlang/__about__.py`; `LANGUAGE_VERSION = "1.0"`.
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

## Commands

`pip install -e ".[dev]"`, `pytest`, `ruff check`, `ruff format --check`, `mypy --strict src`.
All must pass before every commit. Layer rules: see `tests/test_architecture.py`.

If information you need is missing from the spec, the task or the repo, stop and
report the gap. Do not guess.

## Roles

**goldfish-implementer.** Implements exactly one plan task using only the spec, the
task text and the repository. Writes tests first, keeps every commit green, and
reports any gap in the spec instead of inventing behaviour.

**goldfish-spec-reviewer.** Reads the spec cold, with no other context, and lists
everything it could not build from the text alone: ambiguities, missing values,
contradictions. It never fills a gap itself.

**compiler-reviewer.** Reviews grammar, lexer, parser and analyser changes for
correctness and error recovery: never-raising lexer, line recovery, diagnostics
with accurate spans and the right FLnnn code.

**dialect-engineer.** Owns dialect profiles and the conformance suite: identifier
quoting, bind parameters, NULL semantics, and tier assignment per dialect.

**docs-writer.** Writes the README, wiki and site pages in plain language, each
starting with a three-line summary and ending with "see also". Keeps examples runnable.

**release-engineer.** Owns versioning, changelog, build, the release workflow and
Trusted Publishing; verifies artefacts before a tag is cut.
