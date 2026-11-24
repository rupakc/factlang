# FactLang v0.1.0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the FactLang library (language front end, standard verb pack, SQL rendering for 10 native dialects + sqlglot bridge, execution engine, CLI, agent tools), with CI, PyPI release, docs site and wiki.

**Architecture:** A layered compiler pipeline — lexer → parser → analyser → contract plan (IR) → optimizer → SQL lowering → native printer or sqlglot bridge → connector → judge. Front end and IR are stdlib-only; dialects are data (`DialectProfile`); verbs are data (`Contract` in a `ContractPack`).

**Tech Stack:** Python 3.10–3.13, hatchling, pytest, hypothesis, ruff, mypy --strict, sqlglot (optional), duckdb (tests), GitHub Actions, mkdocs-material + mkdocstrings, Pyodide (playground).

**Spec:** `docs/specs/2026-11-24-factlang-design.md` — every task's implementer reads the spec sections named in the task. Section numbers below (§n) refer to it.

## Global Constraints

- Package `factlang`, `src/` layout, import name `factlang`, version `0.1.0` in `src/factlang/__about__.py`; `LANGUAGE_VERSION = "1.0"`.
- Python `>=3.10`; CI matrix 3.10, 3.11, 3.12, 3.13.
- Core (everything except `factlang/sql/sqlglot_bridge.py`, `factlang/connectors/<adapter>.py` other than `base.py`/`callable.py`/`dbapi.py`, `factlang/agent/mcp.py`) imports only the stdlib. Optional deps imported lazily inside functions.
- Layer rule §5.1: a module imports only from strictly lower layers.
- `mypy --strict src` clean, `ruff check` clean, `ruff format --check` clean — all blocking.
- Diagnostic codes are `FLnnn` exactly as listed in §12; ranges fixed.
- Metric aliases (wire format, §8.1): `row_count non_null distinct_keys max_rows_per_key min_value max_value sum_value avg_value duplicate_keys server_now`.
- No input sampling, ever (§8.1). No raw SQL in claims. Every literal is a typed bind parameter.
- MIT license, `Copyright (c) 2026 Rupak Chakraborty`.
- No mention anywhere of the company, product, datasets or table names of the project FactLang originated in. Example tables use a generic shop domain: `orders, customers, order_items, products, events, opportunities`.
- Commits: conventional-commit style (`feat:`, `fix:`, `test:`, `docs:`, `ci:`, `chore:`), each commit leaves `pytest` green. Every commit ends with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Stage explicit paths only (never `git add -A`).

## Review Focus

1. **Empty table / all-NULL column** — every measurable verb must give `NO_DATA`, never `PASS` (Task 19 execution matrix: `empty` and `all_null` fixture tables per verb).
2. **CRLF line endings, a UTF-8 BOM, tabs, and binary garbage in a claim file** — lexer/parser never raise and report per-line diagnostics with correct columns (Task 2 + Task 4 property tests: `test_crlf_equivalent_to_lf`, `test_bom_ignored`).
3. **Identifiers that need quoting** — spaces, embedded quotes, reserved words (`order`, `select`), uppercase in Snowflake/Oracle — render to SQL that names the intended object (Task 14/15 test `test_identifier_rules[profile]`; Task 19 executes `"Order Items"` and a column named `order` on DuckDB).
4. **Exact numbers** — `SUM` over `NUMERIC`/`DECIMAL` returns `Decimal`, integers beyond 2^53, `== 0.1+0.2`-style float noise: comparisons must not lose precision (Task 12 `test_decimal_exact_compare`, Task 19 `test_sum_decimal_column`).
5. **Hostile literals** — a filter string containing `'`, `;`, `--`, `\` must be bound, never spliced (Task 13 `test_literal_never_in_sql_template`, Task 19 executes `WHERE note = 'x''; DROP TABLE orders; --'` and asserts the table still exists).

---

## Dependency graph (fan-out)

```
T1 → T2 → T3 → T4
          T3 → T5 → T6 → T7
                    T6 → T8 → T9 → {T10, T11, T12}  (parallel)
               T8 → T13 → T14 → {T15, T16} (parallel)
T14 + T6 → T17 ;  T13 → T18
{T9..T12, T14, T17, T18} → T19 → T20
T19 → T21 → T22
T21 → {T23, T24} (parallel) → T25
Amendment A: T4 fix round → T26 → T5 ;  T22 → T27 → T25
```
Tasks in braces may be dispatched to parallel goldfish; all others sequentially.

---

### Task 1: Repository skeleton, CI, Claude scaffolding

**Files:**
- Create: `pyproject.toml`, `LICENSE`, `README.md` (stub: title, one-paragraph pitch, "under construction"), `CHANGELOG.md`, `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1), `SECURITY.md`, `.gitignore`, `src/factlang/__init__.py`, `src/factlang/__about__.py`, `src/factlang/py.typed`
- Create: `CLAUDE.md`, `AGENTS.md`, `MEMORY.md`
- Create: `.claude/agents/{goldfish-implementer,goldfish-spec-reviewer,compiler-reviewer,dialect-engineer,docs-writer,release-engineer}.md`
- Create: `.claude/skills/{add-verb,add-dialect,add-connector,run-conformance,release,egm-handoff}/SKILL.md`
- Create: `.github/workflows/ci.yml`
- Test: `tests/test_architecture.py`

**Interfaces:**
- Produces: `factlang.__version__ == "0.1.0"`, `factlang.LANGUAGE_VERSION == "1.0"`; `tests/test_architecture.py` exposing `CORE_MODULES` and `LAYERS` (dict module-prefix → layer int, copied from §5.1) used by every later task.

- [ ] **Step 1:** `pyproject.toml`: hatchling backend; `requires-python = ">=3.10"`; no runtime deps; extras exactly as §11 (`sqlglot = ["sqlglot>=25"]`, `sqlalchemy = ["sqlalchemy>=2"]`, `bigquery = ["google-cloud-bigquery>=3"]`, `snowflake = ["snowflake-connector-python>=3"]`, `redshift = ["redshift-connector>=2"]`, `databricks = ["databricks-sql-connector>=3"]`, `postgres = ["psycopg[binary]>=3.1"]`, `duckdb = ["duckdb>=1.0"]`, `mysql = ["pymysql>=1.1"]`, `clickhouse = ["clickhouse-connect>=0.7"]`, `mssql = ["pyodbc>=5"]`, `oracle = ["oracledb>=2"]`, `mcp = ["mcp>=1.0"]`, `all` = union, `dev = ["pytest>=8","pytest-cov","hypothesis>=6","ruff","mypy","build","twine","duckdb>=1.0","sqlglot>=25"]`, `docs = ["mkdocs-material","mkdocstrings[python]"]`); `[project.scripts] factlang = "factlang.cli:main"`; `[project.entry-points."factlang.packs"]` empty table; ruff (line-length 100, target py310, select `E,F,W,I,B,UP,SIM,RUF`); mypy strict; pytest `testpaths=["tests"]`, markers `integration`, `live`; coverage `fail_under = 90` on `src/factlang` omitting adapters/mcp/sqlglot_bridge.
- [ ] **Step 2:** Write `tests/test_architecture.py` with two tests: `test_core_is_stdlib_only` (walk AST of every core module; allowed top-level imports = `sys.stdlib_module_names` ∪ `{"factlang"}`; imports inside function bodies are allowed) and `test_layering` (for every `from factlang.X import` / `import factlang.X` at module level, the imported module's layer < importer's layer, or same-layer only when listed in §5.1 as the same row-pair `parser`/`formatter`). Both pass trivially on the skeleton; each has a negative-control test that writes a temp module violating the rule and asserts the checker function flags it.
- [ ] **Step 3:** `ci.yml`: on push to `main` + PR. Jobs: `lint` (ruff check, ruff format --check, mypy --strict src), `test` (matrix py 3.10–3.13 × ubuntu-latest, macos-latest, windows-latest; `pip install -e .[dev]`; `pytest -m "not integration and not live" --cov`), `build` (needs lint+test; `python -m build`; `twine check dist/*`; `actions/upload-artifact@v4` name `dist`). Concurrency group per ref, cancel-in-progress for PRs only.
- [ ] **Step 4:** Claude scaffolding. `CLAUDE.md`: project summary (5 lines), commands (`pip install -e .[dev]`, `pytest`, `ruff check`, `ruff format --check`, `mypy --strict src`), the Global Constraints above verbatim, "Where things live" (the §5.1 table), the EGM workflow (§15) and "how to add a verb/dialect/connector" pointers to the skills. `AGENTS.md`: same rules for any coding agent + the six roles with one paragraph each. `MEMORY.md`: header explaining format (`## YYYY-MM-DD — decision` / Why / How to apply) and first entries: the 2+3 dialect decision, from-scratch decision, filter-last grammar rule, paramstyle at render time. Each `.claude/agents/*.md` has frontmatter (`name`, `description`, `tools`) and a body stating its inputs (spec path + task), its output, and its hard rules (goldfish: "use only the spec, the task and the repo; if information is missing, stop and report the gap — do not guess"). Each skill `SKILL.md` has frontmatter (`name`, `description`) and the exact checklist for that operation (e.g. add-verb: one file in `packs/standard/` or the user pack, ≥3 valid + ≥3 invalid examples, register, run `pytest tests/unit/packs tests/conformance`).
- [ ] **Step 5:** Run `pip install -e .[dev] && pytest && ruff check && ruff format --check && mypy --strict src` → all green.
- [ ] **Step 6:** Commit `chore: repository skeleton, CI and agent scaffolding`; push; confirm the CI run is green with `gh run watch`.

---

### Task 2: Diagnostics, tokens, lexer

**Files:**
- Create: `src/factlang/diagnostics.py`, `src/factlang/syntax/__init__.py`, `src/factlang/syntax/tokens.py`, `src/factlang/syntax/lexer.py`
- Test: `tests/unit/syntax/test_diagnostics.py`, `tests/unit/syntax/test_lexer.py`, `tests/property/test_lexer_props.py`

**Interfaces:**
- Produces:
  - `Span(line: int, col: int, end_col: int)` (1-based, end exclusive), frozen.
  - `Severity(Enum)`: `ERROR="error"`, `WARNING="warning"`, `INFO="info"`.
  - `Diagnostic(code: str, severity: Severity, message: str, span: Span | None, hint: str | None = None, suggestion: str | None = None)`; `.to_dict() -> dict[str, Any]`.
  - `DiagnosticBag`: `.error(code, message, span, *, hint=None, suggestion=None)`, `.warning(...)`, `.info(...)`, `.extend(other)`, `__iter__`, `__len__`, `.has_errors -> bool`, `.codes() -> list[str]`, `.render(source: str) -> str` (one block per diagnostic: `error[FL300]: message`, `--> line:col`, the source line, a caret line `^^^` under `col..end_col`, then `help: hint` / `did you mean: X?`), `.to_list() -> list[dict]`.
  - `TokenKind(Enum)`: `IDENT QIDENT NUMBER PERCENT DURATION CARDINALITY STRING ARROW OP DOT COMMA LBRACKET RBRACKET LPAREN RPAREN COMMENT NEWLINE ERROR EOF`.
  - `Token(kind: TokenKind, text: str, value: object, span: Span)` — `value`: NUMBER → `decimal.Decimal` (underscores removed); PERCENT → `Decimal` ratio (`95%` → `Decimal("0.95")`); DURATION → `int` seconds; STRING/QIDENT → unescaped `str`; OP → canonical (`=` stays `=`; parser decides); others → `text`.
  - `tokenize(source: str, bag: DiagnosticBag) -> list[Token]` — always ends with `EOF`; strips a leading BOM; treats `\r\n` and `\r` as newline; `\`+newline continuation emits nothing (dangling `\` before EOF → `FL205`).

- [ ] **Step 1: Write failing tests** in `test_lexer.py`, one per rule in §4.2, including: `test_cardinality_before_number` (`"N:1 1:1"` → two CARDINALITY), `test_duration` (`"24h"` → DURATION 86400; `"24hours"` → IDENT? no: NUMBER `24` then IDENT `hours`), `test_two_char_ops_first` (`">="` one OP), `test_arrow_not_negative` (`"a->b"` → IDENT ARROW IDENT), `test_negative_number` (`"-5"` NUMBER -5), `test_percent_normalised` (`"95%"` → Decimal("0.95")), `test_underscore_digits` (`"1_717"` → 1717), `test_string_escape` (`"'it''s'"` → `it's`), `test_qident_double_quote_and_backtick` (`'"Order Items"'`, `` `x``y` `` → `x`y`), `test_unterminated_string_FL101`, `test_unexpected_char_FL102` (`"a:b"` → FL102 at col 2, ERROR token, lexing continues), `test_comment_kept`, `test_continuation_joins_lines`, `test_dangling_continuation_FL205`, `test_crlf_equivalent_to_lf`, `test_bom_ignored`, `test_spans_are_1_based`. `test_diagnostics.py`: `test_render_caret` (exact multi-line string for one FL102), `test_to_dict_keys == {"code","severity","message","span","hint","suggestion"}`.
- [ ] **Step 2:** `pytest tests/unit/syntax -v` → FAIL (import errors).
- [ ] **Step 3:** Implement. Hand-written scanner over the string (no regex per character is required; regexes for token classes are fine).
- [ ] **Step 4:** Property test `test_tokenize_never_raises` (`hypothesis.strategies.text()` incl. control chars, 500 examples) and `test_spans_within_source`.
- [ ] **Step 5:** `pytest tests -v` → PASS; lint/type clean.
- [ ] **Step 6:** Commit `feat(syntax): diagnostics and lexer`.

---

### Task 3: AST and parser

**Files:**
- Create: `src/factlang/syntax/ast.py`, `src/factlang/syntax/parser.py`; Modify: `src/factlang/syntax/__init__.py`
- Create: `docs/reference/grammar.md` (normative grammar: §4.2–§4.5 EBNF + rules, with ≥25 `factlang-valid` and ≥20 `factlang-invalid FLnnn` fenced blocks)
- Test: `tests/unit/syntax/test_parser.py`, `tests/conformance/test_grammar_doc.py`

**Interfaces:**
- Consumes: Task 2 tokens/diagnostics.
- Produces (all frozen dataclasses with `span: Span`):
  - `Name(text: str, quoted: bool)`; `Ref(parts: tuple[Name, ...])`; `Edge(left: Ref, right: Ref)`
  - `Literal(kind: LiteralKind, value: object)` with `LiteralKind(Enum)`: `NUMBER PERCENT DURATION STRING IDENT QIDENT CARDINALITY BOOL DATE TIMESTAMP LIST` (LIST value = `tuple[Literal, ...]`; DATE/TIMESTAMP value = the raw ISO `str`, validated later)
  - `Attribute(name: str, op: str | None, value: Literal)`
  - filter nodes: `Comparison(column: Name, op: str, value: Literal)`, `InList(column: Name, values: tuple[Literal, ...], negated: bool)`, `IsNull(column: Name, negated: bool)`, `Between(column: Name, low: Literal, high: Literal)`, `And(items: tuple[Cond, ...])`, `Or(items: tuple[Cond, ...])`, `Not(item: Cond)`; `Cond` = their union; `Filter(condition: Cond)`
  - `Claim(verb: str, verb_span: Span, operands: tuple[Ref | Edge | Literal, ...], attributes: tuple[Attribute, ...], filter: Filter | None, comment: str | None, line_text: str)`
  - `Directive(namespace: Ref, comment: str | None)`
  - `Document(items: tuple[Claim | Directive, ...], source: str)`
  - `parse_tokens(tokens: list[Token], bag: DiagnosticBag) -> Document`
  - `ParseResult(document: Document, diagnostics: DiagnosticBag)`; `parse(source: str) -> ParseResult` exported from `factlang.syntax`.

- [ ] **Step 1: Failing tests** for: verb must be all-uppercase IDENT (lowercase first token → FL203 "expected a verb"); operands end at first uppercase bare IDENT; edge parsing; refs with QIDENT parts; attribute with/without op; list with trailing comma; unclosed list FL202; attribute without value FL201; duplicate attribute FL204; `WHERE` starts filter and runs to end of line; precedence `NOT > AND > OR` (`a = 1 OR b = 2 AND c = 3` → `Or(a, And(b, c))`); `x = NULL` → FL206; malformed filter FL206; an uppercase word after a filter is part of the filter (→ FL206); `USE a.b` directive; `USE` mid-line is an ordinary IDENT → treated as attribute name; recovery: a file with 5 bad lines yields ≥5 diagnostics and the good lines still parse (`test_recovery_per_line`); comments preserved on claim.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Recursive-descent parser; on error, record diagnostic and skip to next NEWLINE.
- [ ] **Step 4:** `test_grammar_doc.py`: extract fenced blocks from `docs/reference/grammar.md`; each line of a `factlang-valid` block must parse with zero errors **at the syntax level only** (verb registry not involved); each `factlang-invalid FLnnn` block must produce that code. Blocks whose code is `FL3xx` are skipped here and run in Task 7.
- [ ] **Step 5:** `pytest` → PASS. Commit `feat(syntax): parser and normative grammar`.

---

### Task 4: Formatter and parser property tests

**Files:** Create `src/factlang/syntax/formatter.py`; Test `tests/unit/syntax/test_formatter.py`, `tests/property/test_parser_props.py`

**Interfaces:**
- Produces: `format_claim(claim: Claim) -> str`, `format_document(doc: Document) -> str`. Canonical form: single spaces; `=` in attributes → `==` (filter `=` stays `=`); numbers via `Decimal.normalize()` without exponent; percents printed as `%` when the source was PERCENT; durations printed in the largest exact unit; strings single-quoted with `''`; names re-quoted only if `quoted`; attributes in **source order** (sorting happens only in the hash); comment appended as `  # text`; continuation lines joined.

- [ ] **Step 1:** Unit tests: `test_canonical_spacing`, `test_eq_canonicalised`, `test_comment_preserved`, `test_quoted_names_roundtrip`.
- [ ] **Step 2:** Property tests: generator of *valid* claims built from AST strategies (verbs from a fixed list, random refs/attrs/filters); `test_parse_format_parse_fixpoint` (`format(parse(format(x))) == format(x)`), `test_parse_never_raises` (arbitrary text, 500 examples), `test_crlf_equivalent_to_lf` at document level.
- [ ] **Step 3:** Implement; run; commit `feat(syntax): canonical formatter`.

---

### Task 5: Value types, refs, signatures, verb registry

**Files:** Create `src/factlang/semantics/__init__.py`, `semantics/types.py`, `semantics/refs.py`, `semantics/signature.py`; Test `tests/unit/semantics/test_types.py`, `test_signature.py`

**Interfaces:**
- Produces:
  - `refs.py` (layer 5): `Ident(text: str, quoted: bool)`; `TableRef(catalog: Ident | None, schema: Ident | None, table: Ident)` with `.parts() -> tuple[Ident, ...]` (non-None slots) and `.key() -> tuple[str | None, str | None, str]`; `ColumnRef(table: TableRef, column: Ident)`; `EdgeRef(left: ColumnRef, right: ColumnRef)`.
  - `types.py`: `ValueType(name: str, accepts: frozenset[LiteralKind], coerce: Callable[[Literal], object] | None = None, validate: Callable[[object], str | None] | None = None)` — `validate` returns an error message or None. Built-ins registered under these exact names: `NUMBER` (NUMBER→Decimal), `INTEGER` (NUMBER with no fraction→int), `RATIO` (PERCENT or NUMBER, must be 0≤x≤1 → Decimal), `STRING`, `SYMBOL` (IDENT or STRING → str), `CARDINALITY`, `DURATION` (→int seconds), `TEMPORAL` (DATE/TIMESTAMP → `datetime.date`/`datetime` aware UTC), `NUMBER_OR_TEMPORAL`, `TYPE_FAMILY` (SYMBOL restricted to the 13 families of §6.1), `COLUMN_LIST` (LIST of IDENT/QIDENT → `tuple[Ident, ...]`, non-empty), `VALUE_LIST` (LIST of NUMBER/STRING/BOOL → `tuple[str, ...]` canonical text), `RANGE_PAIR` (LIST of exactly 2 NUMBER_OR_TEMPORAL), `SEPARATOR` (STRING of 1–3 chars from `: ; | / - = # > ~ , + (` and space). `register_type(vt: ValueType) -> None` (raises on duplicate unless `replace=True`), `get_type(name: str) -> ValueType`.
  - `signature.py`: `OperandKind(Enum)`: `TABLE COLUMN EDGE`; `OperandSpec(name: str, kind: OperandKind)`; `AttrSpec(name: str, type: str, required: bool = False, ops: tuple[str, ...] = (), doc: str = "")` — `ops == ()` means an operator is forbidden; non-empty means one of them is required; `VerbSpec(name: str, summary: str, operands: tuple[OperandSpec, ...], attrs: tuple[AttrSpec, ...] = (), filterable: bool = False, requires: tuple[tuple[str, str], ...] = ())` (`requires`: (a, b) = if a present then b required) — `__post_init__` validates name all-uppercase, attr names uppercase and unique, attr names not equal to `WHERE`; `VerbRegistry`: `.register(spec)`, `.get(name) -> VerbSpec | None`, `.names() -> list[str]`, `.suggest(name: str) -> str | None` (`difflib.get_close_matches(n=1, cutoff=0.6)`).

- [ ] Steps: failing tests for every built-in type (accept/reject/coerce), `VerbSpec` validation errors, `suggest("UNIQE") == "UNIQUE"`; implement; run; commit `feat(semantics): value types and verb signatures`.

---

### Task 6: Catalogs, filter expressions, CheckedClaim

> **Amendment A (2026-11-24, spec §18 — owner-directed, URGENT):** `filters.py` adds `FBool(column: Ident)` for the bare boolean atom (R9, spec §4.5); its canonical form is `FCompare(column, '=', Param(..., True, 'boolean'))` so `deleted` and `deleted = TRUE` canonicalise identically. Test: `test_bare_bool_canonical_equals_eq_true`.

**Files:** Create `semantics/catalog.py`, `semantics/filters.py`, `semantics/checked.py`; Test `tests/unit/semantics/test_catalog.py`, `test_checked.py`

**Interfaces:**
- Produces:
  - `catalog.py` (layer 6): `Catalog` Protocol per §9.2 with `TableRef`; `NullCatalog` (always `None`); `StaticCatalog(tables: Mapping[str, Mapping[str, str]])` keyed by dotted lower-cased table key (`"schema.table"` or `"table"`), `.from_json(path)`; `CachedCatalog(inner: Catalog, ttl_s: float = 300, max_tables: int = 10_000)` — LRU, caches `None` answers too.
  - `filters.py` (layer 5): resolved filter IR — `Param(name: str, value: object, family: str)` where family ∈ `integer decimal float string boolean date timestamp`; `FCompare(column: Ident, op: str, param: Param)` (op ∈ `= != < <= > >=`, `==`→`=`), `FIn(column, params: tuple[Param, ...], negated)`, `FIsNull(column, negated)`, `FBetween(column, low: Param, high: Param)`, `FAnd(items)`, `FOr(items)`, `FNot(item)`; `FilterExpr` union; `canonical(expr) -> str` (stable text used by the hash: commutative `FAnd`/`FOr` items sorted by their canonical text; `FIn` params sorted).
  - `checked.py`: `CheckedAttr(op: str | None, value: object, type: str)`; `CheckedClaim(verb: str, operands: tuple[TableRef | ColumnRef | EdgeRef, ...], attrs: Mapping[str, CheckedAttr], filter: FilterExpr | None, canonical_text: str, semantic_hash: str, span: Span, source_text: str, comment: str | None)` with helpers `.table -> TableRef` (first operand's table / edge left table), `.column -> ColumnRef` (raises `TypeError` if none), `.edge -> EdgeRef`, `.has(name) -> bool`, `.value(name) -> object`, `.op(name) -> str | None`, `.number(name) -> Decimal`.

- [ ] Steps: tests for StaticCatalog lookup case-insensitivity on unquoted parts, CachedCatalog TTL (inject `clock` callable) and LRU eviction, filter canonical ordering (`a=1 AND b=2` ≡ `b=2 AND a=1`), CheckedClaim helpers; implement; commit `feat(semantics): catalogs, filter IR, checked claims`.

---

### Task 7: Analyzer and semantic identity

> **Amendment A (2026-11-24, spec §18 — owner-directed, URGENT):** (R3) JOIN's `WITH`/`TO` pair: FL304 naming the missing one when only one is present, FL309 on unequal length — add both to the inline-registry tests. (R9) FL305 when the catalog knows a bare-boolean column is not boolean; `semantic_hash` equal for `deleted` and `deleted = TRUE`.

**Files:** Create `semantics/analyzer.py`, `semantics/identity.py`; Test `tests/unit/semantics/test_analyzer.py`, `test_identity.py`; Modify `tests/conformance/test_grammar_doc.py` (run FL3xx blocks with the standard pack once Task 10 exists — mark those `xfail(strict=False)` until then; Task 12 removes the xfail).

**Interfaces:**
- Produces:
  - `analyze(document: Document, registry: VerbRegistry, *, catalog: Catalog | None = None, bag: DiagnosticBag) -> list[CheckedClaim]` — applies `USE` directives (§4.4, right-aligned slots, §4.3), resolves operands per `OperandSpec.kind` (ref depth per kind; COLUMN needs ≥2 parts; TABLE ≤3; else FL302), checks operand count (FL301), attributes (FL303 with suggestion, FL304 missing required / `requires`, FL305 type via `ValueType`, FL306 op rules, FL309 `validate` failures and invalid ISO temporals), `COLUMN_LIST` must not repeat the key column (FL307), filter allowed (FL308) and converts AST filter → `FilterExpr` with typed `Param`s (names `p1, p2, …` in source order), catalog checks (FL311 on definite absence; FL313 info when catalog returned `None`; FL312 warning when an EDGE's two column types differ in family and no `CAST` attr). Never raises.
  - `semantic_hash(verb: str, operands: tuple, attrs: Mapping[str, CheckedAttr], filter: FilterExpr | None) -> str` — `"sha256:" + hexdigest` over JSON (`sort_keys=True`, separators `(",",":")`) of: verb; operands as nested lists of `[text, quoted]` with empty slots as `null`; attrs sorted by name as `[name, op_canonical, canonical_value_text]` (op `=`→`==`); `canonical(filter)` or `null`.

- [ ] **Step 1:** Tests using a small inline registry (`JOIN`, `NULLS`, `VALUES` specs built in the test): one test per FL3xx code; `test_use_applies_to_both_edge_sides`; `test_hash_ignores_comment_whitespace_attr_order_numeric_spelling` (`MATCH >=0.99` ≡ `MATCH >= 99%`); `test_hash_changes_on_value_op_operand_filter`; `test_hash_same_under_equivalent_use` (`USE s` + `t.c` ≡ `s.t.c`); `test_never_raises` (hypothesis over random documents).
- [ ] **Step 2–4:** fail → implement → pass. Commit `feat(semantics): analyzer and semantic identity`.

---

### Task 8: IR and judge

> **Amendment A (2026-11-24, spec §18 — owner-directed, URGENT):** (R2) `ValueMembership.declared` may be empty; `report_limit` 1..1000 (already). (R3) `KeyOverlap(left: tuple[ColumnRef, ...], right: tuple[ColumnRef, ...], cast_to=None, measure_multiplicity=False, filter=None, right_filter: FilterExpr | None = None)` — non-empty, equal length, each side's columns on one table (`__post_init__`). (R4) `KeyOverlap.result_columns()` adds `distinct_right`, `left_non_null`, `matched_left_rows`. (R1) `ir.canonical_key(plan) -> str` (deterministic JSON of the plan) used for measure de-duplication.

**Files:** Create `src/factlang/ir/__init__.py`, `ir/metrics.py`, `ir/primitives.py`, `src/factlang/judge/__init__.py`, `judge/predicate.py`, `judge/outcome.py`, `judge/run.py`; Test `tests/unit/ir/test_primitives.py`, `tests/unit/judge/test_predicate.py`, `test_judge.py`

**Interfaces:**
- Produces:
  - `Metric(Enum)` members `ROW_COUNT NON_NULL DISTINCT_KEYS MAX_PER_KEY MIN_VALUE MAX_VALUE SUM AVG DUPLICATE_KEYS NOW`; `ALIASES: Mapping[Metric, str]` exactly §8.1; `Arity(Enum)`: `SINGLE_ROW ROW_SET`.
  - Primitives (frozen dataclasses, each with class attribute `arity` and `result_columns()`), fields exactly §8.1 using `TableRef`/`ColumnRef`/`Ident`/`FilterExpr` from semantics: `ColumnAggregate(table, column: Ident | None, metrics: tuple[Metric, ...], filter=None)`, `CompositeKeyAggregate(table, columns: tuple[Ident, ...], metrics, filter=None)`, `ValueMembership(table, column: Ident, declared: tuple[str, ...], split: str | None = None, report_limit: int = 20, filter=None)`, `KeyOverlap(left: ColumnRef, right: ColumnRef, cast_to: str | None = None, measure_multiplicity: bool = False, filter=None)`, `PatternCoverage(table, column: Ident, pattern: str, filter=None)`, `MetadataQuery(table, column: Ident | None = None)`. `__post_init__` validation: metrics non-empty, `MAX_PER_KEY`/`DUPLICATE_KEYS` require a column, `report_limit` 1..1000, pattern not only wildcards, split per SEPARATOR rule. `ProbePlan` = union; `register_primitive(cls: type, *, lower: Callable) -> None` stores into `ir.PRIMITIVE_LOWERINGS` (consumed by Task 13).
  - `judge/predicate.py`: `Predicate` Protocol `.evaluate(facts, claim) -> tuple[bool | None, str]` and `.reads() -> tuple[set[str], set[str]]` (facts read, attrs read — used by FL600/601 tests); `Compare(fact, op, value=None, attr=None)`, `CompareUsingClaimOp(fact, attr)` (uses the op the author wrote), `Within(fact, attr, tolerance_attr="TOLERANCE", abs_attr="ABS_TOLERANCE")` (for answer verbs: target from `attr`, tolerance rules §6.4), `And(*items)`, `Or(*items)`, `Not(item)`, `WhenClaimHas(attr, then, otherwise)`. Comparisons are done in `Decimal` when both sides convert exactly (`Decimal(str(x))` for floats), `==` uses `math.isclose(rel_tol=1e-9, abs_tol=1e-9)` only when either side is a float. Three-valued: missing/None fact → `(None, "<fact> was not measured")`; `And` → False if any False else None if any None; `Or` dual.
  - `judge/outcome.py`: `Verdict` (§7), `Evidence(sql: str | None, params: tuple, dialect: str | None, tier: str | None, duration_ms: float | None, bytes_processed: int | None, fused_with: tuple[str, ...])`, `Outcome(claim_text, canonical_text, semantic_hash, verb, verdict, explanation, facts, evidence, diagnostics: tuple[dict, ...])` with `.to_dict()` (JSON-safe: Decimal→str, datetime→ISO).
  - `judge/run.py`: `payload_for(arity, rows) -> dict | None` (SINGLE_ROW → `rows[0]`; ROW_SET → `{"rows": rows}`; no rows → None); `decide_facts(derive, decide, claim, payload) -> tuple[Verdict, str, dict]` — never raises (derive exception → `ERROR` with `"derive failed: <Type>: <msg>"`; undecidable → `NO_DATA`).

- [ ] Steps: tests for each primitive's validation; aliases table equality; predicate truth tables incl. None propagation; `test_decimal_exact_compare` (`Decimal("0.3") == 0.1+0.2` passes via isclose; `Decimal("12345678901234567890") != Decimal("12345678901234567891")`); judge with raising derive → ERROR. Commit `feat(ir,judge): measurement primitives and three-valued judgement`.

---

### Task 9: Contract, ContractPack, decorator API, completeness

**Files:** Create `src/factlang/contract.py`; Test `tests/unit/test_contract.py`, `tests/conformance/test_contract_completeness.py`

**Interfaces:**
- Produces:
  - `Example(source: str, valid: bool, expect: str | None = None)` (invalid requires `expect`).
  - `Contract(spec: VerbSpec, derive: Callable[[dict, CheckedClaim], dict], decide: Predicate, examples: tuple[Example, ...], plan: Callable[[CheckedClaim], ProbePlan] | None = None, catalog_facts: Callable[[CheckedClaim, Catalog], dict | None] | None = None, unprobeable_reason: str | None = None, fact_names: tuple[str, ...] = ())` — exactly one of `plan`/`catalog_facts`/`unprobeable_reason` (else `ValueError`); `fact_names` declares what `derive` can produce (used by FL600).
  - `ContractPack(name: str, version: str)`: `register(c)` (dup → `ValueError`), `override(c)` (stores previous in `.overridden[name]`), `alias(new: str, target: str)` (alias resolves at `get`; spec cloned with new name), `remove(name)`, `extend(name, version) -> ContractPack` (child that resolves misses in parent; copy-on-write), `merge(other, on_conflict="error"|"keep"|"replace")`, `get(name) -> Contract | None`, `names()`, `contracts()`, `verb_registry() -> VerbRegistry` (fresh each call), `verb(name, *, summary="", operands, attrs=(), filterable=False, requires=()) -> ContractBuilder` decorator: the decorated function is the plan (or use `.catalog(fn)` / `.unprobeable(reason)`), builder methods `.derive(fn)`, `.decide(pred)`, `.example(source, valid=True, expect=None)`, `.facts(*names)`; the builder registers into the pack on first `pack.get`/`names` call, raising `ValueError` naming the missing part if incomplete.
  - `discover_packs() -> dict[str, Callable[[], ContractPack]]` reading entry-point group `factlang.packs` via `importlib.metadata`.
  - `OperandSpec` convenience: `Operand.table(name="table")`, `Operand.column(name="column")`, `Operand.edge(name="edge")` (in `signature.py`, add in this task).
- `test_contract_completeness.py` provides `assert_pack_complete(pack)` used for every pack: FL600 (decide reads a fact not in `fact_names`), FL601 (decide reads an attr not in spec), FL602 (fewer than 3 valid + 3 invalid examples), every example behaves as declared through `parse`+`analyze` with `NullCatalog`. Message prefix is the code.

- [ ] Steps: tests for each pack operation incl. alias + override interplay and child-pack isolation (`extend` then `register` in child doesn't affect parent); decorator incomplete → error; completeness negative controls (a deliberately broken contract triggers each FL6xx). Commit `feat: contracts, packs and extension API`.

---

### Tasks 10–12: Standard pack (parallel)

Common to all three:
- **Files:** `src/factlang/packs/__init__.py` (Task 10 only), `src/factlang/packs/standard/__init__.py` (Task 10 creates `standard_pack() -> ContractPack` returning a fresh pack `("standard", "1.0")` that registers every module listed in `_MODULES`; Tasks 11/12 append their modules), one file per verb `src/factlang/packs/standard/<verb_lower>.py` exporting `CONTRACT`.
- **Tests:** `tests/unit/packs/test_<verb>.py` per verb: examples via `assert_pack_complete`; `plan()` returns the primitive/metrics in §6; `derive` on hand-built payloads (normal, zero rows, all NULL, Decimal); `decide` PASS/FAIL/NO_DATA.
- Attribute names, ops, facts, decisions: **exactly §6** tables and the "Exact fact definitions" list. Each contract's `spec.summary` is one sentence; each example uses the generic shop domain.

### Task 10: EXISTS, TYPE, ROWS, NULLS, DEAD, UNIQUE, FANOUT
- EXISTS/TYPE use `catalog_facts`. TYPE attr `IS` type `TYPE_FAMILY`, no op. Family normalisation happens in the catalog adapter (Task 17) — `catalog.columns()` values passed to TYPE are already families when the catalog is warehouse-backed; for `StaticCatalog` values are taken as written, lower-cased.
- ROWS: `VALUE` (NUMBER, ops all six, required) → `CompareUsingClaimOp("row_count", "VALUE")`.
- NULLS: `RATE` (RATIO, ops `== <= < >= >`, required). DEAD: no attrs, `Compare("null_rate", "==", value=1)`.
- UNIQUE/FANOUT: `WITH` (COLUMN_LIST, optional) switches plan to `CompositeKeyAggregate(columns=(column,)+WITH)`. FANOUT `MAX_PER_KEY` (INTEGER, ops `<= < ==`, required).
- Commit `feat(pack): structure and data-quality verbs`.

### Task 11: JOIN, VALUES, RANGE, PATTERN, FRESH

> **Amendment A (2026-11-24, spec §18 — owner-directed, URGENT):** JOIN gains `WITH` (COLUMN_LIST, left extra key columns) + `TO` (COLUMN_LIST, right extra key columns) and `COVERAGE` (RATIO, ops `>= == <= < >`, optional); derive also `coverage`, `row_match_rate`, `fan_in`, `distinct_left`, `distinct_right`, `matched`, `left_non_null` exactly per spec §6.3 Amendment A (all `None` when the denominator is 0); decide adds `WhenClaimHas("COVERAGE", CompareUsingClaimOp("coverage","COVERAGE"), true)`. ≥3 valid + ≥3 invalid examples covering composite keys and COVERAGE.
- JOIN: `MATCH` (RATIO, ops `>= ==`, required), `CARD` (CARDINALITY, optional), `CAST` (TYPE_FAMILY, optional); `measure_multiplicity = has("CARD")`; decide = `And(CompareUsingClaimOp("match_rate","MATCH"), WhenClaimHas("CARD", <card predicate>, <true>))` where card facts derive booleans `card_ok` per §6.3. Fact `card_ok` is `None` when no keys matched.
- VALUES: `IN` (VALUE_LIST, required, empty list allowed), `SPLIT` (SEPARATOR), `ALLOW` (RATIO, ops `<=`). Derive from `ValueMembership` rows per §8.1.
- RANGE: `BETWEEN` (RANGE_PAIR, required, low ≤ high else FL309 via `validate`).
- PATTERN: `LIKE` (STRING, required, validated: lowercase-folded on plan; reject pattern of only `%`/`_` via FL309), `RATE` (RATIO, required).
- FRESH: `WITHIN` (DURATION, required). Derive per §6.2 exact definitions; inject `now` only via `server_now` column.
- Commit `feat(pack): relationship, membership and freshness verbs`.

### Task 12: COUNT, DISTINCT, SUM, AVG, MIN, MAX, NOTE
- Each: `VALUE` (NUMBER — MIN/MAX: NUMBER_OR_TEMPORAL; ops all six, required), `TOLERANCE` (RATIO, no op), `ABS_TOLERANCE` (NUMBER, no op); `TOLERANCE` and `ABS_TOLERANCE` mutually exclusive (FL309 via a spec-level `exclusive` pair — add `exclusive: tuple[tuple[str,str],...] = ()` to `VerbSpec` and its analyzer check in this task); decide `Within("value", "VALUE")`.
- NOTE: operand COLUMN, `IS` (SYMBOL, required), `BECAUSE` (STRING, required), `unprobeable_reason="semantic fact; recorded, not measured"`.
- Remove the xfail added in Task 7. `test_decimal_exact_compare` at contract level.
- Commit `feat(pack): answer-fact verbs and NOTE`.

---

### Task 13: SQL AST, lowering, RenderedProbe

> **Amendment A (2026-11-24, spec §18 — owner-directed, URGENT):** (R3/R4) lower `KeyOverlap` over key tuples (exclude a tuple with any NULL part on each side; compare part-wise; `CAST` per part) and emit `distinct_right`, `left_non_null`, `matched_left_rows`; `right_filter` applied to the right side's distinct-key subquery. (R2) `ValueMembership` with empty `declared` lowers with no IN-list (every non-NULL value undeclared). (R9) lower `FBool`. (R6) add `sql/fragment.py`: `RenderedFragment(sql_template, params, dialect, tier)` with `bind`/`inline` shared with `RenderedProbe`, plus `lower_filter(filter, alias) -> Expr` and `lower_join(edges, filters, aliases) -> FromItem` (left-most table's filter in WHERE-part returned separately, others ANDed into ON).

**Files:** Create `src/factlang/sql/__init__.py`, `sql/ast.py`, `sql/lower.py`, `sql/probe.py`; Test `tests/unit/sql/test_lower.py`, `test_probe.py`

**Interfaces:**
- Consumes: primitives (Task 8), `FilterExpr`/`Param` (Task 6).
- Produces:
  - `sql/ast.py`: frozen node classes named exactly as §8.2 (`Select(columns: tuple[Aliased,...], from_: FromItem, where: Expr | None, group_by: tuple[Expr,...], order_by: tuple[OrderItem,...], limit: int | None)`, `Aliased(expr, alias)`, `TableName(ref: TableRef)`, `SubqueryFrom(select, alias)`, `Join(left, right, on, kind="LEFT")`, `Column(name: Ident, qualifier: str | None)`, `ParamRef(param: Param)`, `Agg(fn: str, arg: Expr | None, distinct: bool)` fn ∈ `COUNT SUM AVG MIN MAX`, `CountIf(cond)`, `Lower(e)`, `Trim(e)`, `Like(e, pattern: ParamRef)`, `Cast(e, family)`, `Coalesce(*e)`, `Case(whens, else_)`, `Now()`, `Compare(op, l, r)`, `And(items)`, `Or(items)`, `Not(e)`, `InList(e, items, negated)`, `IsNull(e, negated)`, `Between(e, lo, hi)`, `Literal(value)` (only for constants FactLang itself writes: 0, 1, `'summary'`), `UnionAll(selects)`, `Star()`).
  - `lower(plan: ProbePlan) -> Select | UnionAll` — the single place encoding §8.1 NULL rules: `MAX_PER_KEY`/`DUPLICATE_KEYS` via subquery grouping by key columns `WHERE every key IS NOT NULL`; composite `DISTINCT_KEYS` via `COUNT(*)` over a grouped subquery, never concatenation; `PatternCoverage` `CountIf(Like(Lower(col), param))` with pattern lower-cased into the Param; `ValueMembership` = `UnionAll(summary_select, ranked_undeclared_select(limit=report_limit))`; `KeyOverlap` = distinct non-NULL left keys `LEFT JOIN` distinct right keys, plus multiplicity subqueries when requested; filters lowered from `FilterExpr`. Custom primitives dispatched through `ir.PRIMITIVE_LOWERINGS`; unknown → `RenderError("FL500 …")`.
  - `sql/probe.py`: `RenderError(ValueError)`; `RenderedProbe(sql_template: str, params: tuple[Param, ...], arity: Arity, plan: ProbePlan, dialect: str, tier: str)` — template placeholders are `{{p1}}`-style tokens produced by the printer; `.bind(paramstyle: str) -> tuple[str, tuple | dict]` for `qmark` (`?`), `numeric` (`:1`), `named` (`:p1`), `format` (`%s`), `pyformat` (`%(p1)s`), and `"bigquery"` (`@p1`); `.inline(quote_string: Callable[[str], str]) -> str` display-only.
- [ ] Tests: lowering snapshots as **structure assertions** (e.g. `MAX_PER_KEY` select's inner where contains `IsNull(negated=True)` for every key column; no node anywhere has `limit` except `ValueMembership`'s ranked select); `test_literal_never_in_sql_template` (a hostile string param appears only in `params`); bind for all six styles. Commit `feat(sql): internal SQL AST, lowering and probes`.

---

### Task 14: Native printer, DuckDB + Postgres profiles, dialect conformance suite

> **Amendment A (2026-11-24, spec §18 — owner-directed, URGENT):** (R6) `render_filter(filter, *, table, dialect, alias=None) -> RenderedFragment` and `render_join(edges, *, dialect, filters=None, aliases=None) -> RenderedFragment` in `sql/registry.py` (exported as `fl.sql.render_filter`/`render_join`); conformance: both render on every profile, hostile literal only in params; DuckDB executes `SELECT COUNT(*) ` + render_join output and equals a hand-written join. Add `SQLGLOT_NAMES['trino'] = 'trino'`.

**Files:** Create `sql/profile.py`, `sql/printer.py`, `sql/registry.py`, `sql/profiles/__init__.py`, `sql/profiles/duckdb.py`, `sql/profiles/postgres.py`; Test `tests/conformance/test_dialects.py`, `tests/unit/sql/test_printer.py`

**Interfaces:**
- Produces:
  - `DialectProfile` frozen dataclass with every field in §8.3 (`name, tier, quote_char, unquoted_case, reserved_words: frozenset[str], max_table_parts, max_identifier_length, count_if_style: Literal["countif","count_if","sum_case","filter"], limit_style: Literal["limit","top","fetch"], cast_types: Mapping[str, str], now_sql: str, string_type: str, type_families: tuple[tuple[str, str], ...], param_types: Mapping[str, str], information_schema_sql: str, overrides: Mapping[type, Callable] = {}`).
  - `print_select(node, profile) -> tuple[str, tuple[Param, ...]]`; `render(plan, profile) -> RenderedProbe` (guards: starts with `SELECT`/`WITH`; no `;`; FL505 on too many table parts; FL502 identifier rules §8.4).
  - `sql/registry.py`: `register_profile(p, *, replace=False)`, `get_profile(name) -> DialectProfile | None`, `native_dialects() -> list[str]`, `renderer_for(dialect: str, *, prefer: Literal["native","sqlglot"] = "native") -> Callable[[ProbePlan], RenderedProbe]` (§8.3 resolution order; FL504 message lists natives and `pip install factlang[sqlglot]`).
  - `family_of(native_type: str, profile) -> str` (unwrap `Nullable(…)`/`LowCardinality(…)`, scale-0 decimals → integer, then first regex match, else `other`).
- `test_dialects.py` is **parametrised over `native_dialects()`** so Task 15 profiles join automatically: every primitive × every metric renders; aliases appear exactly; forbidden tokens (`TABLESAMPLE`, `SAMPLE`, and `LIMIT`/`TOP`/`FETCH` outside ValueMembership); `test_identifier_rules[profile]` (unquoted stays unquoted; reserved word quoted with case fold; quoted keeps case; embedded quote doubled; NUL → FL502); if sqlglot importable, `sqlglot.parse_one(sql, read=<sqlglot name>)` succeeds (`SQLGLOT_NAMES` map in `registry.py`: bigquery→bigquery, snowflake→snowflake, redshift→redshift, postgres→postgres, duckdb→duckdb, databricks→databricks, clickhouse→clickhouse, tsql→tsql, mysql→mysql, oracle→oracle); `test_type_families[profile]` against fixture `tests/fixtures/type_names/<profile>.json` (`{native: family}`, ≥15 entries each).
- [ ] Also an execution smoke test on DuckDB (in-memory) for every primitive in `tests/unit/sql/test_duckdb_exec.py` (duckdb is a dev dependency, not integration-marked).
- [ ] Commit `feat(sql): native printer with duckdb and postgres profiles`.

### Task 15: Remaining native profiles

> **Amendment A (2026-11-24, spec §18 — owner-directed, URGENT):** (R7) also `sql/profiles/trino.py` (+ `tests/fixtures/type_names/trino.json`): tier `verified`; double-quote identifiers; `unquoted_case='lower'`; `count_if_style='count_if'`; `limit_style='limit'`; `now_sql='current_timestamp'`; `param_types` (`integer→bigint, decimal→decimal, float→double, string→varchar, boolean→boolean, date→date, timestamp→timestamp(6) with time zone`); namespace slots catalog.schema.table (3). Commit message adds `trino`.
**Files:** `sql/profiles/{bigquery,snowflake,redshift,databricks,clickhouse,tsql,mysql,oracle}.py` + `tests/fixtures/type_names/<each>.json`. Tiers per §8.5. Namespace slots per §4.3 table. Notable spellings: BigQuery `COUNTIF`, backtick quote, `CURRENT_TIMESTAMP()`, `param_types` (`integer→INT64, decimal→NUMERIC, float→FLOAT64, string→STRING, boolean→BOOL, date→DATE, timestamp→TIMESTAMP`); Snowflake `COUNT_IF`, upper case; T-SQL `TOP`, `[`…`]` quoting is **not** used (use `"` with QUOTED_IDENTIFIER semantics), `SYSUTCDATETIME()`; Oracle `FETCH FIRST`, upper case, `SYS_EXTRACT_UTC(SYSTIMESTAMP)`; MySQL backtick, `UTC_TIMESTAMP()`; ClickHouse `countIf`, `now()`; Databricks backtick, `count_if`, `current_timestamp()`; Redshift like Postgres but `GETDATE()` and `sum_case`. Conformance suite from Task 14 must pass for all. Commit `feat(sql): bigquery, snowflake, redshift, databricks, clickhouse, tsql, mysql, oracle profiles`.

### Task 16: sqlglot bridge
**Files:** `sql/sqlglot_bridge.py`; Test `tests/unit/sql/test_sqlglot_bridge.py` (skipped if sqlglot missing).
**Interfaces:** `to_sqlglot(node) -> sqlglot.exp.Expression`; `render_sqlglot(plan, dialect: str) -> RenderedProbe` (tier `best_effort`, placeholders as `{{pN}}` identical to native so `bind` works). `renderer_for(..., prefer="sqlglot")` and the non-native fallback call it. Tests: renders for `trino`, `presto`, `athena`, `hive`, `teradata`, `starrocks`; **cross-check**: for every primitive, DuckDB execution of native SQL and of `render_sqlglot(plan, "duckdb")` returns identical rows on the Task 19 fixture set (fixtures created inline here). Commit `feat(sql): sqlglot bridge for long-tail dialects`.

---

### Task 17: Connectors and WarehouseCatalog

> **Amendment A (2026-11-24, spec §18 — owner-directed, URGENT):** (R8) `connectors/base.py`: `run_metadata(connector, sql, params=(), *, timeout_s=None) -> QueryResult` per spec §9.1 Amendment A — guard first (one statement; no `;` outside literals; first keyword in `SELECT WITH SHOW DESCRIBE DESC`; else `ExecutionError` code `FL703`), then build a ROW_SET `RenderedProbe` (plan `None`/metadata marker, dialect from the connector) and call the connector's normal execute. Tests: guard rejects `DELETE`, `SELECT 1; DROP TABLE x`, `CREATE …`; accepts `SHOW TABLES`, a `WITH` query; DuckDB `information_schema.tables` query returns rows. (R7) extra `trino = ["trino>=0.330"]` in pyproject; `DBAPIConnector` works over `trino.dbapi`. (R5) confirm `dry_run_bytes` stays optional on the protocol.

**Files:** `src/factlang/connectors/{__init__,base,callable,dbapi,sqlalchemy,bigquery}.py`, `src/factlang/semantics/warehouse_catalog.py` → **place at `src/factlang/connectors/catalog.py`** (layer 14, it needs connectors); Test `tests/unit/connectors/test_dbapi_duckdb.py`, `test_callable.py`, `test_catalog_duckdb.py`; BigQuery/SQLAlchemy tested with fakes.
**Interfaces:**
- `QueryResult(rows: list[dict[str, Any]], bytes_processed: int | None = None)`; `Connector` Protocol: `dialect: str`, `paramstyle: str`, `execute(probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult`, optional `dry_run_bytes(probe) -> int | None`; `AsyncConnector` with `aexecute`; `ExecutionError(Exception)` with `.code` (`FL700`/`FL702`).
- `CallableConnector(fn: Callable[[str, tuple | dict], list[dict]], dialect: str, paramstyle: str = "qmark")`.
- `DBAPIConnector(connection, dialect: str, paramstyle: str | None = None)` — paramstyle defaults to the driver module's `paramstyle` attribute (found via `type(connection).__module__` root), else `qmark`; rows via `cursor.description`.
- `SQLAlchemyConnector(engine)` — dialect from `engine.dialect.name` (map `postgresql→postgres`, `mssql→tsql`, `mariadb→mysql`, others identity); executes `text()` with `named` binding.
- `BigQueryConnector(client, *, default_project=None, default_dataset=None, maximum_bytes_billed=None)` — `paramstyle="bigquery"`, typed `ScalarQueryParameter`/`ArrayQueryParameter` from `Param.family` via the profile's `param_types`; `dry_run_bytes` via `QueryJobConfig(dry_run=True)`.
- `connect(dsn: str) -> Connector` (requires SQLAlchemy; clear ImportError message naming the extra).
- `WarehouseCatalog(connector)` implementing `Catalog` using each profile's `information_schema_sql` through a `MetadataQuery` probe; column types mapped with `family_of`.
- Commit `feat(connectors): DB-API, SQLAlchemy, BigQuery and callable connectors`.

### Task 18: Optimizer (dedupe + fusion)
**Files:** `src/factlang/optimizer.py`; Test `tests/unit/test_optimizer.py`.
**Interfaces:** `WorkItem(claim: CheckedClaim, plan: ProbePlan)`; `FusedProbe(plan: ProbePlan, members: tuple[tuple[str, Mapping[str, str]], ...])` (member hash, its alias → fused alias map); `optimize(items: list[WorkItem], *, fuse: bool = True) -> list[FusedProbe]` — dedupe by `semantic_hash`; fuse `ColumnAggregate` with equal `(table.key(), canonical(filter))` across columns by building a `FusedAggregate` primitive (new, in `optimizer.py`, registered via `register_primitive` with a lowering that emits one `Select` with aliases `m{i}__{alias}`); same for `CompositeKeyAggregate` only when columns are identical; `split(fused: FusedProbe, rows) -> dict[str, list[dict]]` back to member-shaped rows. Tests: two NULLS claims on different columns of one table → one probe; different filters → two; split restores exact aliases. Commit `feat: probe de-duplication and fusion`.

### Task 19: Engine, Report, execution matrix on DuckDB

> **Amendment A (2026-11-24, spec §18 — owner-directed, URGENT):** (R1) `Checker.measure(plans) -> list[Measurement]` / `ameasure` and `Measurement(plan, payload, evidence, error)` per spec §9.3 Amendment A — same render → cost-guard → execute → split path, de-dup by `ir.canonical_key`, fusion of compatible aggregates; never raises on execution failure. (R5) `Checker.estimate(items) -> list[CostEstimate]` — renders, calls `dry_run_bytes` if present, never executes (assert with a call-logging fake connector). Fixture additions: `refs_parent` with 1,000 keys and `refs_child` referencing 3 of them (forward match 1.0, coverage 0.003); a 2-part composite key pair with one NULL part row. Tests: every primitive via `measure` returns the payload the matching claim's derive receives; composite JOIN PASS/FAIL/NO_DATA; COVERAGE FAIL on the asymmetric pair; `ValueMembership(declared=())` top-k equals a reference GROUP BY.
**Files:** `src/factlang/engine.py`; `tests/integration/conftest.py` (fixture warehouse builder), `tests/integration/test_verbs_duckdb.py`.
**Interfaces:** `Checker(connector: Connector | None = None, *, dialect: str | None = None, pack: ContractPack | None = None, catalog: Catalog | None = None, concurrency: int = 4, timeout_s: float | None = 60, max_bytes: int | None = None, fuse: bool = True, prefer: str = "native")`; `.check(text: str) -> Report`, `.acheck(text) -> Report` (asyncio; uses `aexecute` if present else `asyncio.to_thread`), `.compile(text) -> list[RenderedProbe | Outcome]`; `Report(outcomes: tuple[Outcome, ...], diagnostics: DiagnosticBag)` with `.ok` (no FAIL/ERROR/INVALID), `.summary() -> dict[Verdict, int]`, `.to_dict()`, `.to_json(indent=2)`, `.to_markdown()`. Flow: parse → analyze (catalog default per §6.1) → per claim: INVALID (with its diagnostics) / UNPROBEABLE / catalog verbs / plan → optimize → render → (cost guard FL701) → execute (thread pool `concurrency`, FL700/FL702 → ERROR) → split → judge → Outcome with Evidence (`sql` = `probe.inline(...)`). Order of outcomes = source order.
- Fixture builder `make_shop(conn)` creates tables with known answers: `customers`(10 rows, unique id), `orders`(100 rows; 5 NULL `shipped_at`; `customer_id` 98 matching + 2 orphan; `status` in open/closed/churned + 3 rows `'other: late'`), `order_items` (fan-out 3 per order max), `events` (max `created_at` = now − 1h), `empty_t` (0 rows), `all_null_t` (10 rows, column all NULL), `"Order Items"` with a column named `order`.
- `test_verbs_duckdb.py`: for **every** standard verb a PASS, FAIL and NO_DATA case (NO_DATA on `empty_t`/`all_null_t`) with exact expected facts; `test_sum_decimal_column`; hostile literal test (Review Focus 5); quoted identifier test (Review Focus 3); fused vs unfused produce identical outcomes.
- Commit `feat: checker engine with reports`.

### Task 20: Integration CI on real engines

> **Amendment A (2026-11-24, spec §18 — owner-directed, URGENT):** (R7) add service `trinodb/trino:latest` (port 8080, catalog `memory`), install the `trino` extra in the job, add `trino` to `FACTLANG_REQUIRE_ENGINES`; fixture builder must create tables in `memory.default`.
**Files:** `tests/integration/test_verbs_engines.py` (parametrised over available engines; same expectations as Task 19 reused from a shared `EXPECTATIONS` table moved to `tests/integration/expectations.py`); Modify `.github/workflows/ci.yml` adding job `integration` with services `postgres:16`, `mysql:8.4`, `clickhouse/clickhouse-server:24.8`, `mcr.microsoft.com/mssql/server:2022-latest` (+ ODBC driver 18 install step), env DSNs, `pytest -m integration`. Engines absent locally are skipped; in CI they are required (`FACTLANG_REQUIRE_ENGINES=postgres,mysql,clickhouse,tsql`). `tests/live/` skeleton for bigquery/snowflake/redshift/databricks/oracle gated by `FACTLANG_LIVE_<DIALECT>=1`. Commit `ci: execute the verb matrix on postgres, mysql, clickhouse and sql server`.

### Task 21: Public API facade and CLI

> **Amendment A (2026-11-24, spec §18 — owner-directed, URGENT):** (R1, R6, R10) exports also `measure, parse_filter, Measurement, CostEstimate, RenderedFragment, run_metadata` (and `fl.sql.render_filter`/`render_join` reachable via `sql`); `parse_filter(text, *, catalog=None, table=None)` accepts the condition with or without a leading `WHERE`. `tests/unit/test_api.py` asserts the full export list and runs the spec §10 Amendment A block on DuckDB.
**Files:** `src/factlang/api.py`, `src/factlang/__init__.py`, `src/factlang/cli.py`, `src/factlang/__main__.py`; Test `tests/unit/test_api.py`, `tests/unit/test_cli.py`.
**Interfaces:** `factlang` exports exactly: `parse, lint, format, compile, check, Checker, Report, Outcome, Verdict, standard_pack, ContractPack, Contract, Example, Operand, Attr (=AttrSpec), Compare, CompareUsingClaimOp, Within, And, Or, Not, WhenClaimHas, Metric, ir, sql, types, DBAPIConnector, CallableConnector, SQLAlchemyConnector, BigQueryConnector, connect, StaticCatalog, NullCatalog, CachedCatalog, WarehouseCatalog, __version__, LANGUAGE_VERSION`. `lint(text, *, pack=None, catalog=None) -> DiagnosticBag`; `format(text) -> str`; `compile(text, *, dialect, pack=None) -> list[RenderedProbe]`; `check(text, *, connector, **checker_kwargs) -> Outcome | Report` (one claim → Outcome, else Report). CLI per §10 with exit codes 0/1/2/3; `--format json` emits `Report.to_json()`. Test the spec's §10 example block verbatim (with a DuckDB connector). Commit `feat: public API and command-line interface`.

### Task 22: Agent surface and MCP server
**Files:** `src/factlang/agent/{__init__,tools,reference}.py`, `src/factlang/agent/mcp.py`; Test `tests/unit/agent/test_tools.py`, `test_reference.py`, `test_mcp.py` (skipped without `mcp`).
**Interfaces:** `tool_definitions(pack=None) -> list[dict]` — three tools `factlang_check(claims: str)`, `factlang_lint(claims: str)`, `factlang_explain(claims: str, dialect: str)`, each `{"name","description","input_schema"}`; the check tool's description embeds `verb_reference(pack, style="compact")`. `verb_reference(pack=None, style="markdown"|"compact") -> str` generated from specs + valid examples only. `handle_tool_call(name: str, arguments: dict, checker: Checker) -> dict` (unknown tool → `{"error": ...}`). `mcp.serve(checker)` stdio server registering the three tools; CLI `factlang mcp --dsn URL`. Tests: reference lists every verb in the pack; a custom verb added via `extend` appears; `handle_tool_call` round-trip on DuckDB. Commit `feat(agent): LLM tool definitions, verb reference and MCP server`.

---

### Task 23: Documentation site + playground

> **Amendment A (2026-11-24, spec §18 — owner-directed, URGENT):** Docs: `docs/guide/measure.md` (raw measurement, estimate, metadata SQL), `docs/extending/fragments.md` (render_filter/render_join), Trino in dialect tables, FL703 in `docs/reference/diagnostics.md`, API reference marks the R10 items as stable public API.
**Files:** `mkdocs.yml`, `docs/index.md` (landing: problem → claim → verdict, "where it shines": agent answer verification, schema/registry validation, data contracts in CI, migration parity checks, LLM eval grounding), `docs/getting-started.md`, `docs/guide/{language,verbs,filters,namespaces,verdicts,evidence,batching}.md`, `docs/extending/{verbs,dialects,connectors,catalogs,packs,types,primitives}.md`, `docs/agents.md`, `docs/cli.md`, `docs/reference/{grammar (exists),verbs,diagnostics,api}.md`, `docs/playground.md` + `site-playground/{playground.js,playground.css}`, `scripts/gen_reference.py` (writes `docs/reference/verbs.md` from `standard_pack()`), `.github/workflows/docs.yml`; Test `tests/conformance/test_diagnostics_doc.py` (codes in `docs/reference/diagnostics.md` == codes emitted in `src/` by regex `"FL\d{3}"`), `tests/conformance/test_docs_examples.py` (every ```` ```python ```` block in `docs/getting-started.md` and `docs/extending/verbs.md` executes against DuckDB).
- Playground: loads Pyodide from the official CDN, installs the built wheel from the site (`docs.yml` copies `dist/*.whl` into the site), offers editor + dialect dropdown + buttons Lint / Format / Explain SQL; output panel renders diagnostics with carets. In-browser execution (DuckDB-WASM) only after a feasibility spike recorded in `MEMORY.md`; playground must work without it.
- `docs.yml`: on push to main: build wheel, `python scripts/gen_reference.py`, `mkdocs build --strict`, deploy with `actions/deploy-pages` (or `peaceiris/actions-gh-pages` to `gh-pages` branch); repo Pages source = gh-pages.
- Commit `docs: documentation site and interactive playground`.

### Task 24: GitHub Wiki

> **Amendment A (2026-11-24, spec §18 — owner-directed, URGENT):** Add a `Measurement-API` page and mention Trino in Dialects; Design-Decisions gains an Amendment A entry.
**Files:** `docs/wiki/*.md` — exactly the pages listed in §14.3 (Home, Concepts, Architecture, Language-Guide, Lexer, Parser, Analyzer, IR, Dialects, sqlglot-Bridge, Optimizer, Connectors, Catalogs, Judge, Standard-Verbs, Extending-Verbs, Extending-Dialects, Extending-Connectors, Extending-Catalogs, Extending-Packs, Agent-Integration, CLI, Diagnostics, FAQ, Glossary, Contributing, Design-Decisions, `_Sidebar.md`); each starts with a 3-line summary and ends with "See also"; Architecture includes an ASCII + Mermaid pipeline diagram. `.github/workflows/wiki.yml`: on push to main touching `docs/wiki/**`, clone `https://x-access-token:${{ secrets.GITHUB_TOKEN }}@github.com/${{ github.repository }}.wiki.git`, rsync `docs/wiki/`, commit, push (`permissions: contents: write`). Test `tests/conformance/test_wiki_links.py` (every `[[Page]]`/relative link resolves to a file in `docs/wiki/`). **Prerequisite (owner):** first wiki page created in the web UI. Commit `docs: GitHub wiki`.

### Task 25: Release v0.1.0
**Files:** `.github/workflows/release.yml`, final `README.md`, `CHANGELOG.md` 0.1.0 entry.
- `release.yml`: on tag `v*`: build (sdist+wheel), verify tag == `__version__`, `twine check`, job `pypi` with `environment: pypi`, `permissions: id-token: write`, `pypa/gh-action-pypi-publish@release/v1`; job `github-release` with `softprops/action-gh-release@v2` attaching `dist/*` and generated notes.
- README per §14.3 (badges: CI, PyPI, Python versions, license, docs).
- Verify: `pip install factlang==0.1.0` in a fresh venv, run the README 30-second example against DuckDB.
- Commit `chore(release): 0.1.0`; tag `v0.1.0`; push tag.

---

### Task 26: Bare boolean filter atom in parser, formatter and grammar (Amendment A R9) — DO NEXT

> Retroactive to completed Tasks 3–4. Sequencing (builder ruling, 2026-11-24): runs immediately after the in-flight Task 4 formatter fix round's re-review (same files), before Task 5 — not in parallel. Bare `TRUE`/`FALSE` atom → FL206 (add a `factlang-invalid FL206` block `ROWS orders WHERE TRUE`); see spec §4.5 rulings.

**Files:** Modify `src/factlang/syntax/parser.py`, `src/factlang/syntax/formatter.py`, `docs/reference/grammar.md` (filter EBNF + `factlang-valid` blocks `ROWS orders WHERE deleted`, `ROWS orders WHERE deleted IS NULL OR NOT deleted`, `ROWS orders WHERE NOT (paid) AND amount > 0`, and a `factlang-invalid FL206` block `ROWS orders WHERE deleted deleted`); Test `tests/unit/syntax/test_parser.py`, `tests/unit/syntax/test_formatter.py`.
**Interfaces:** filter AST gains a bare-name atom node (name it consistently with the existing filter AST, e.g. `FilterName(name)`), produced when an atom is a `name` not followed by an operator/`IN`/`IS`/`BETWEEN`/`NOT IN`. Formatter prints it back bare. Parser keeps never-raise and per-line recovery; property tests (`test_crlf_equivalent_to_lf`, idempotent format) stay green.
- [ ] Steps: failing tests → implement → `pytest && ruff check && ruff format --check && mypy --strict src` → commit `feat(syntax): bare boolean filter atom (Amendment A R9)`.

### Task 27: Amendment A acceptance sweep — after Task 22
**Files:** `tests/integration/test_amendment_a.py`.
- One test per row of spec §18's table (R1–R10) asserting its acceptance criterion end to end on DuckDB (and Trino in CI); a test that greps `docs/reference/diagnostics.md` for FL703. Commit `test: Amendment A acceptance sweep`. Report done to the SchemaGuru Elephant.

---

## Self-review notes (Elephant)
- Spec coverage: §4 → T2–T4,T7; §5 → T1 architecture test; §6 → T10–T12; §7 → T8; §8 → T13–T16; §9 → T17–T19; §10 → T21–T22; §11 → T1,T25; §12 → T2–T9,T23; §13 → spread, matrix T19–T20; §14 → T1,T20,T23–T25; §15 → T1 scaffolding.
- Spec gaps closed here (also to be back-ported into the spec): `exclusive` attr pairs on `VerbSpec` (T12); `catalog_facts` third contract mode (T9); `WarehouseCatalog` placed in `connectors/catalog.py` (layer 14) because it needs a connector; `FusedAggregate` primitive lives in the optimizer.
