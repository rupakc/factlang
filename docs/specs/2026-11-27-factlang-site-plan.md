# FactLang site redesign + live-verdict playground — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development (Elephant-Goldfish: one fresh implementer per task in a private worktree, one fresh reviewer per task). Steps use checkbox (`- [ ]`) syntax.

**Goal:** Rebuild the GitHub Pages landing page in the Storming Bastille structure, restyle the docs, and make the playground return real verdicts on a sample database and on the visitor's CSV files, in the browser.

**Architecture:** MkDocs Material stays. FactLang runs in Pyodide (`site-playground/glue.py`); SQL runs in DuckDB-WASM in a Web Worker (`site-playground/engine.js`); an `AsyncConnector` in glue bridges them with tagged-JSON value marshalling. Build-time facts come from the code via `scripts/docs_hooks.py`.

**Tech Stack:** MkDocs Material 9, vanilla JS (ES modules, no npm), Pyodide 0.29.0, `@duckdb/duckdb-wasm@1.32.0` via jsDelivr `+esm`, Python Playwright for e2e.

**Spec:** `docs/specs/2026-11-27-factlang-site-design.md` (binding; read all of it).

## Global Constraints

- No change under `src/factlang/` and no version bump; library public API untouched.
- No forbidden predecessor/company names anywhere; MIT; copyright Rupak Chakraborty.
- Third-party code only from jsDelivr at exact pinned versions: Pyodide `v0.29.0`, `@duckdb/duckdb-wasm@1.32.0` (`/+esm`), mvp/eh bundles only, worker via Blob URL + `importScripts`.
- Playground sources only in `site-playground/` (published flat to `assets/playground/`); never `docs/assets/playground/`; fixtures under `tests/`.
- `playground.js`/`.css` are inert when `#fl-playground` is absent; DuckDB/engine code is lazily `import()`ed; init idempotent across instant navigation.
- In the browser use `Checker.acheck` only (never `Checker.check`); `Checker(conn, dialect="duckdb", catalog=StaticCatalog(families), timeout_s=30, concurrency=1)`; catalog types via `family_of(type, get_profile("duckdb"))`.
- `glue.py` imports neither `js` nor `duckdb` at module level (docs.yml clean-venv smoke must pass).
- Every number/list on the landing page is generated from or asserted against the code. No invented users, logos, testimonials or benchmarks.
- WCAG AA contrast; all motion off under `prefers-reduced-motion`; responsive to 360 px; light and dark.
- Gates per task: `pytest -q`, `ruff check --no-cache`, `ruff format --check`, `mypy --strict src` (unchanged), `python -m build --wheel` then `mkdocs build --strict` (use a throwaway venv with `.[docs]` if the repo venv lacks mkdocs). Use `PYTHONPATH=<worktree>/src`.
- Commit only in your worktree branch; never push, never tag.

## Review Focus

1. A DECIMAL/BIGINT/DATE result crossing the JS↔Python boundary changes a verdict or reason text — expect identical outcomes to `factlang.check` on DuckDB (T3 parity test).
2. A CSV whose file or column names are hostile (spaces, quotes, leading digits, `customers.csv` clashing, unicode, CR in a header) — expect a valid table name, correct FactLang spelling, or "not addressable", never a crash (T3 naming tests + T5 e2e fixture).
3. Visiting the landing page or any docs page downloads Pyodide/DuckDB — expect none (T5 e2e network assertion).
4. A slow query or a dead worker leaves the page hung — expect an ERROR outcome after 30 s and a working page (T3 timeout test + T5 e2e).
5. Instant navigation away and back creates a second Pyodide/worker — expect exactly one (T4 idempotence + T5 e2e).

---

### Task 1: Visual system and docs restyle

**Files:** Modify `docs/assets/factlang.css`, `docs/assets/factlang.js`, `mkdocs.yml` (palette/primary-accent custom colours, Playground nav label only). Test: `tests/conformance/test_site_style.py` (new).

**Interfaces — Produces:** CSS custom properties (light + `[data-md-color-scheme="slate"]` variants) that T2/T4 use: `--fl-bg`, `--fl-bg-subtle`, `--fl-surface`, `--fl-border`, `--fl-text`, `--fl-text-muted`, `--fl-indigo`, `--fl-teal`, `--fl-amber`, `--fl-rose`, `--fl-violet`, `--fl-pass`, `--fl-fail`, `--fl-nodata`, `--fl-error`, `--fl-radius`, `--fl-shadow`; utility classes `.fl-card`, `.fl-badge`, `.fl-fade-up` (+ `.is-visible`, toggled by an IntersectionObserver in `factlang.js`, idempotent under `document$`), `.fl-button`, `.fl-button--solid`, `.fl-section`, `.fl-section--subtle`, `.fl-copy` (copy-to-clipboard button behaviour in `factlang.js`).

- [ ] Write `test_site_style.py`: every `--fl-*` token above is defined in both the light and slate blocks of `factlang.css`; a `prefers-reduced-motion: reduce` block disables `.fl-fade-up` transitions/animations; contrast of `--fl-text` and `--fl-text-muted` on `--fl-bg`/`--fl-surface` ≥ 4.5:1 and of each verdict colour's badge text on its badge background ≥ 4.5:1 in both schemes (parse the hex values; write the WCAG luminance formula in the test). Run → FAIL.
- [ ] Implement tokens, components, docs-page restyle (type scale, admonitions, code blocks, tables, header blur, footer) keeping existing verdict class names used by pages and the ledger. Run → PASS.
- [ ] Gates; screenshot-free self-check by building the site; commit `feat(site): visual system and docs restyle`.

### Task 2: Landing page and build-time facts

**Files:** Modify `docs/overrides/home.html`, `docs/index.md`, `scripts/docs_hooks.py` (add `on_config`), landing styles appended to `docs/assets/factlang.css` in a clearly fenced `/* ── landing ── */` section. Test: `tests/conformance/test_landing_facts.py` (new). Consumes T1 tokens/classes.

**Interfaces — Produces:** `docs_hooks.site_facts() -> dict` with keys `version`, `native_dialects` (int), `verbs` (int), `verdicts` (int), `core_dependencies` (int), `tiers` (`{"verified": [...], "native": [...]}` sorted), all read from the code/`pyproject.toml`; `on_config` stores it as `config.extra["fl_facts"]`; `home.html` renders from it.

- [ ] Write `test_landing_facts.py`: `site_facts()` equals values recomputed independently in the test (`len(fl.sql.native_dialects())`, `len(standard_pack().names())`, `len(Verdict)`, `pyproject` `dependencies == []`, tiers from the dialect registry); after `mkdocs build` into a temp dir (skip with a clear reason if mkdocs is not installed), the rendered `index.html` contains each number in the stat strip and each dialect badge in its tier group; every landing link resolves to a built page; the ledger test in `test_docs_examples.py` still passes. Run → FAIL.
- [ ] Implement the nine sections of spec §5.1 in order, with copy obeying spec §2 (truthfulness) and §5.1 corrections (one-`SELECT` read-only wording, six verdicts, tiers from code). Run → PASS.
- [ ] Gates; commit `feat(site): landing page in the new structure with facts from code`.

### Task 3: Playground engine (Python glue + DuckDB-WASM bridge)

**Files:** Modify `site-playground/glue.py`; create `site-playground/engine.js`, `site-playground/duckdb-worker-boot.js` if needed (flat — no subfolders). Tests: `tests/playground/test_glue_check.py`, `tests/playground/test_marshalling.py`, `tests/playground/test_csv_naming.py`, `tests/playground/test_suggest.py`, fixtures in `tests/playground/fixtures/`. Add `duckdb==<engine version embedded in duckdb-wasm 1.32.0>` as a new optional extra `playground` in `pyproject.toml` (only this line); record how the version was determined in the report.

**Interfaces — Produces (glue.py, all JSON-string in/out for JS unless noted):**
- `class PlaygroundConnector` (`aexecute(probe, *, timeout_s)`), built from an injected `async run(sql: str, params: list[tagged]) -> {"columns": [[name, duckdb_type], ...], "rows": [[tagged, ...], ...]}`.
- Tagged value = `{"t": "<duckdb type family or 'null'>", "v": <str|number|bool|null>}`; `decode_value(tagged)` / `encode_param(value)`.
- `table_name_for(filename: str, existing: list[str]) -> str` (spec §5.2 rule).
- `fl_catalog(tables_json) -> catalog_json` (families via `family_of`); `fl_lint_with(text, catalog_json)`.
- `async fl_check(text, run, catalog_json, timeout_s=30) -> json` = `{"ok": bool, "verified": bool, "counts": {verdict: n}, "outcomes": [Outcome.to_dict() + {"sql": str|None, "params": [[name, value, type]], "elapsed_ms": int|None}], "diagnostics": [...]}`.
- `fl_profile_sql(table_json) -> json list of SQL` and `fl_suggest(table_json, profile_json) -> json list of claim strings` (spec §5.2 suggestion rules).

**Interfaces — Produces (engine.js, ES module):** `export async function startEngine({ pyodide, onStatus }) -> Engine` where `Engine` = `{ loadSample(): Promise<TableInfo[]>, loadCsv(files: File[]): Promise<{tables: TableInfo[], errors: {file, message}[]}>, reset(): Promise<TableInfo[]>, tables(): TableInfo[], check(text): Promise<CheckResult>, lint(text): Promise<LintResult>, suggest(table): Promise<string[]>, terminate(): void }`, `TableInfo = {name, rows, columns: [{name, type, family, spelling|null}], source: "sample"|"csv"}`. Sample SQL is fetched from a single source shared with Getting started (glue exposes `SAMPLE_SQL`, asserted equal to the Getting started first SQL block).

- [ ] Write the four test files: marshalling table for DECIMAL, BIGINT, HUGEINT, DOUBLE, DATE, TIMESTAMP, BOOLEAN, VARCHAR, NULL round-trips; parity — with `run` backed by Python duckdb **through `encode`/JSON/`decode`**, for both sample examples every outcome's verdict, facts and explanation equal `factlang.check` with a `DBAPIConnector` on the same DuckDB database; `TYPE … IS integer` passes via `fl_catalog`; timeout — a `run` that sleeps past a 0.2 s timeout yields an ERROR outcome with FL702; naming cases (`Orders 2024.csv`, `2fa.csv`, `customers.csv` with sample loaded, `données.csv`, `a.b.csv`); suggestions lint clean, deterministic, `paid`/`valid` not matched by the id rule, `NULLS … RATE <=5%` threshold fixed; `SAMPLE_SQL` equals Getting started block 1; glue module imports without `js`/`duckdb`. Run → FAIL.
- [ ] Implement glue functions and `engine.js` (DuckDB-WASM 1.32.0 `+esm`, worker via Blob URL, `read_csv_auto` via registered file handles, 100 MB/file, 200 MB total, 10 files, 30 s timeout → terminate/restart/rebuild/re-register, `runPythonAsync`/Promise bridge, idempotent `startEngine`). Run → PASS.
- [ ] Gates (+ `pip install -e ".[playground]"` in a throwaway venv for the parity test); commit `feat(playground): live verdicts engine — Pyodide + DuckDB-WASM bridge`.

### Task 4: Playground UI

**Files:** Modify `site-playground/playground.js`, `site-playground/playground.css`, `docs/playground.md`. Consumes T3's `startEngine` / `Engine` / `CheckResult` and T1 tokens. Test: `tests/playground/test_playground_page.py` (static: built `playground/index.html` has the data panel, Load CSV control with `accept=".csv"`, Check button, privacy banner text, and no "names are not checked" sentence).

- [ ] Write the static test → FAIL.
- [ ] Implement spec §5.2 UI: data panel, CSV picker + drag-and-drop, Reset, suggestions (insert on click), Check (primary; Ctrl/Cmd+Enter), summary bar (counts, ok/verified in words), outcome cards (badge, facts, explanation, collapsible SQL + params, elapsed), "Check always runs DuckDB" note, per-example default action, the two sample examples as defaults, loading/progress/CDN-failure states, share link (claims only), inert without `#fl-playground`, one engine across instant navigation. Run → PASS.
- [ ] Gates; commit `feat(playground): data panel, CSV loading and verdict cards`.

### Task 5: Tests, CI and guards

**Files:** Create `tests/e2e/test_site_e2e.py`, `tests/e2e/conftest.py`, `tests/playground/fixtures/` CSVs if T3 did not (quoted header, malformed); modify `.github/workflows/docs.yml` (e2e job gating publish), `.github/workflows/ci.yml` (`site` job on site-touching pushes: glue tests with `.[playground]`, build, e2e), `tests/conformance/test_wiki_links.py` (`SCANNED` += `site-playground`, `scripts`, `.github`), `pyproject.toml` (Playwright in a new `e2e` extra only).

- [ ] Write e2e tests (skipped unless `FL_SITE_URL` is set): spec §6 items (1)–(6) and the landing budget (raw bytes < 1.5 MB excluding fonts; no Pyodide/DuckDB/wheel/`assets/playground/*` except `playground.js`/`.css`), CSV-content-never-sent check via request inspection, timeout path via a test-only query parameter lowering the timeout (document it in `engine.js`).
- [ ] Wire CI: build wheel → mkdocs build → serve `site/` on localhost → `playwright install --with-deps chromium` → run e2e with retries for CDN fetches; publish depends on it. Run the e2e locally against a local build; run the guard test.
- [ ] Gates; commit `ci(site): e2e gate, site job, guard scope`.

### Task 6 (controller): whole-branch review, visual review, owner push, deploy verification
Whole-branch review (most capable model) with screenshots at 1440×900 and 390×844 in light and dark of: landing, playground (sample check results; CSV loaded), one guide page. Fix wave if needed. Owner pushes; watch CI + Docs workflow; verify the live site with the e2e suite against `https://rupakc.github.io/factlang/`.
