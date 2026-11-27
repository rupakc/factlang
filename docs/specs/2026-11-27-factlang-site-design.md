# FactLang documentation site — redesign and live-verdict playground

Status: APPROVED by owner 2026-11-27 (cold-read gaps resolved) · Date: 2026-11-27 · Process: Elephant-Goldfish (spec → cold-read → plan → goldfish tasks → per-task reviews → whole-branch review → deploy)
Base: `main` at 0.1.4 (`87c94dd`). Binding parent: `docs/specs/2026-11-24-factlang-design.md` (naming rules, layering, no forbidden names).

## 1. Intent and why

The owner wants the GitHub Pages site (https://rupakc.github.io/factlang/) to be polished, professional, engaging, interactive and enticing — modelled on the owner's Storming Bastille project page — so that a first-time visitor understands **what FactLang is for, what it gives them out of the box, and sees it working on real data within a minute**.

Today the site is MkDocs Material with a custom hero (a six-row claim "ledger" of real outcomes) and ~25 guide/reference pages. The playground runs the real library in the browser (Pyodide) but stops at lint / format / SQL: it never shows a verdict, because there is no data. That is the weakest point for showing the benefit.

**Owner decisions (2026-11-27):** playground option **B** — real verdicts on a built-in sample database **and** on the visitor's own CSV files; follow the Elephant-Goldfish model.

## 2. Goals, non-goals, anti-goals

Goals
- G1 A landing page in the Storming Bastille structure that states the benefits and the out-of-the-box features clearly.
- G2 A playground that returns **real verdicts with evidence** (verdict, measured numbers, reason, exact SQL + parameters) on (a) a built-in sample shop database and (b) CSV files the visitor loads, entirely in the browser.
- G3 A consistent, more refined visual system across the landing page, playground and docs pages, in light and dark.
- G4 Every fact and number the site states is true and machine-checked.

Non-goals
- No change to the `factlang` library or its public API, and no PyPI release. Playground-only Python lives in `site-playground/`.
- No server, account, analytics or telemetry.
- No new guide pages: every landing-page card links to an existing page. Existing guide/reference content is restyled, not rewritten.
- Not the GitHub wiki.

Anti-goals (a failure even if it "works")
- Anything the page says that is not true: invented numbers, invented users/testimonials/logos, benchmark claims not measured here, a verdict shown on the landing page that the real library would not produce.
- Visitor data leaving the browser.
- A slow landing page: it must not download Pyodide, DuckDB or the wheel.
- Any mention of the forbidden predecessor/company names. The guard (`tests/conformance/test_wiki_links.py:124`, `SCANNED`) does not yet scan `site-playground/`, `scripts/` or `.github/`; this work extends it to them.

## 3. Constraints (grounded against the repo and CDNs, 2026-11-27)

- Site stack stays **MkDocs Material** (`mkdocs.yml`, `docs/overrides/`, `docs/assets/`), built and published by `.github/workflows/docs.yml` (runs after green CI on `main`; `mkdocs build --strict`).
- Playground stays **vanilla JS + CSS, no npm/bundler**; third-party code from jsDelivr at **pinned exact versions**:
  - Pyodide `v0.29.0` (already used). It does **not** bundle a `duckdb` Python package.
  - DuckDB-WASM **`@duckdb/duckdb-wasm@1.32.0`** (newest non-dev npm release, MIT; npm's `latest` tag is a dev build — do not use it). Import `https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.32.0/+esm` (the plain `dist/duckdb-browser.mjs` has a bare `apache-arrow` import and fails without an import map). `getJsDelivrBundles()` pins worker/wasm URLs to 1.32.0. **Only the `mvp`/`eh` bundles** (GitHub Pages cannot send COOP/COEP, so never `coi`/pthreads); start the worker via a Blob URL that calls `importScripts(bundle.mainWorker)` (a cross-origin `new Worker(url)` fails). The builder records the DuckDB engine version 1.32.0 embeds and pins the test-time Python `duckdb` to that engine version for the parity tests.
- FactLang supports this **without library changes**:
  - `Checker.acheck(text)` (engine.py:410) runs claims through an `AsyncConnector` (`connectors/base.py:77`, `async def aexecute(probe, *, timeout_s) -> QueryResult`), so a Pyodide connector can `await` a JS promise that runs SQL in DuckDB-WASM. `acheck` uses `asyncio.to_thread`, which works in Pyodide only because its WebLoop runs executors inline — proven by the browser e2e test, not assumed. **Never call `Checker.check` in the browser** (it builds a `ThreadPoolExecutor`). `glue.py` is loaded with synchronous `runPython` today; the check path is called via `runPythonAsync` / a coroutine returned to JS as a Promise.
  - `RenderedProbe.bind(paramstyle)` (sql/probe.py:406) → `(sql, params)`; `normalise_rows(probe, rows)` (base.py:156) and `map_error(exc, *, timed_out=False, timeout_s=None)` (base.py:143) are reused for labels and error codes. **Value types are the real parity risk** (measured: a DECIMAL sum returned as a scaled integer turns a `pass` into a `fail`; a float changes the reason text) — handled by §5.2's marshalling rule.
  - `StaticCatalog({"table": {"col": "<type family>"}})` (semantics/catalog.py:43). Types must be **type families** computed as `WarehouseCatalog` does — `family_of(duckdb_type, get_profile("duckdb"))` (connectors/catalog.py:115) — otherwise `TYPE t.c IS integer` fails where `factlang.check` passes.
  - Dialect `duckdb` is native, **verified** tier. `factlang.syntax.needs_quoting` / `quote_name` give correct FactLang spellings for CSV-derived names.
- Files: playground sources live in **`site-playground/`**, which `scripts/docs_hooks.py` publishes **flat** (non-recursive) to `assets/playground/` with the built wheel and `wheel.json`. Never write into `docs/assets/playground/`; test fixtures live under `tests/`. `playground.js`/`.css` load on every page (`extra_javascript`/`extra_css`), so they must do nothing when `#fl-playground` is absent and lazily `import()` the DuckDB/worker code; init must be idempotent across Material's instant navigation (one Pyodide, one worker).

## 4. Approaches considered

1. **Pyodide runs FactLang; DuckDB-WASM (JS, Web Worker) runs the SQL; an `AsyncConnector` bridges them** — chosen **[call]**. Real async path, verified `duckdb` dialect, no library change, native fast CSV reading.
2. Python `sqlite3` in Pyodide. Rejected: no native SQLite profile (bridge-only `best_effort`, needs sqlglot in the browser), weak CSV typing.
3. Precomputed verdicts only. Rejected: cannot do option B, claims not editable.

Landing page: **keep MkDocs Material and rebuild the home template** (`docs/overrides/home.html` + `docs/index.md`) in Bastille's section structure — chosen **[call]** — so navigation, search, dark mode and docs stay one site.

## 5. The design

### 5.1 Landing page (`docs/overrides/home.html`, `docs/index.md`, `docs/assets/factlang.css`, `scripts/docs_hooks.py`)
Sections, in order:
1. **Hero** — one-line promise, two-sentence explanation, buttons **Try it live** and **Get started**, `pip install factlang` with a copy button, and the existing **claim ledger** of real outcomes. The ledger animates in row by row (CSS classes only; off under `prefers-reduced-motion`); its markup keeps the shape `test_landing_ledger_is_real` (tests/conformance/test_docs_examples.py:146) parses, and `index.md` keeps at least one executed python block (`_run_page` asserts one exists).
2. **Stat strip** — native SQL dialects (11), standard verbs (19), verdicts (6, the `Verdict` enum), core dependencies (0). Injected at build time by `docs_hooks.py` from the code; a test asserts the rendered page shows the code's values.
3. **The idea** — AI agents and analysts state facts about data in prose; a wrong sentence looks like a right one; FactLang makes each sentence one measurable line (adapt "Prose cannot be checked. Claims can.").
4. **Benefits by audience** — three cards: *AI agents & LLM apps* (ground answers; MCP server; claim cap; explain → agents.md); *Data & analytics engineers* (assumptions about data as plain checkable lines; CLI with exit codes for CI; batching & fusion → cli.md, guide/batching.md); *Review & audit* (every verdict carries the exact SQL and measured numbers; when a value cannot be measured the verdict says so — NO_DATA / UNPROBEABLE / ERROR — rather than guessing → guide/evidence.md, guide/verdicts.md).
5. **Out of the box** — six feature cards linking to their guide pages: 19 standard verbs; 11 native dialects + sqlglot bridge; read-only by construction (every claim compiles to one `SELECT`, docs/index.md:159) with the exact SQL as evidence; batching & fusion; diagnostics with codes, carets and fixes (lint/format); extensible (verbs, packs, types, dialects, connectors, primitives).
6. **How a claim becomes a verdict** — numbered steps (parse → analyse → plan → compile to dialect SQL → run → judge with evidence), each with one real artefact.
7. **Works with your warehouse** — dialect badges by tier, generated from code (today verified: clickhouse, duckdb, mysql, postgres, trino, tsql; native: bigquery, databricks, oracle, redshift, snowflake) plus the sqlglot bridge (best_effort) for the long tail.
8. **Quick start** — three copyable steps using the Getting started shop example.
9. **Closing call to action + link cards** — Playground, Getting started, Guide, Agents & MCP, GitHub, PyPI.

Visual system **[call]**, inspired by Bastille: light slate background, white cards, indigo primary with teal/amber/rose/violet accents, subtle gradient hero, fade-up on scroll, blurred sticky header; keep the fonts (Schibsted Grotesk / Fragment Mono) and verdict colour semantics. Dark mode first-class. Responsive to 360 px. WCAG AA contrast. All motion off under `prefers-reduced-motion`.

### 5.2 Playground (`docs/playground.md`, `site-playground/*`)
- **Bridge [call]**: a playground `AsyncConnector` (only `aexecute`) in `glue.py`, taking an injected `async run(sql, params) -> (rows, column_types)`; the browser injects a JS function running the prepared statement in the DuckDB-WASM worker; tests inject a Python-`duckdb` one. `glue.py` imports neither `js` nor `duckdb` at module level (docs.yml's clean-venv smoke keeps passing). **Marshalling both ways**: JS converts each Arrow cell by schema type to tagged JSON (DECIMAL, INT64, HUGEINT as strings; DATE/TIMESTAMP as ISO strings; NULL as null); Python rebuilds `Decimal`/`int`/`date`/`datetime`. Parameters go to JS tagged the same way and are bound with explicit casts. `Checker(conn, dialect="duckdb", catalog=StaticCatalog(families), timeout_s=30, concurrency=1)` — the worker serialises queries.
- **Data panel**: lists loaded tables — by default the **sample shop database** (`customers`, `orders`; SQL equal to the Getting started page's first SQL block, asserted by a test) — with row counts and expandable columns (name, DuckDB type, FactLang spelling).
- **Load CSV**: picker and drag-and-drop; one or many files, **added alongside** the sample tables **[call]**. Table name from the file name: last extension stripped, lower-cased, characters outside `[A-Za-z0-9_]` → `_`, leading digit → `t_` prefix, clash with any loaded table → `_2`, `_3`…; loaded with `read_csv_auto`, types from `DESCRIBE`. Columns keep their CSV names, shown with their FactLang spelling; a name containing CR, LF or NUL is listed as not addressable (`quote_name` refuses it). Limits **[call]**: 100 MB per file, 200 MB total, 10 files; allocation failure → a clear message, never a dead page. A malformed CSV shows DuckDB's error and leaves other tables intact. **Reset** drops the CSV tables, keeps the sample. Banner: *files are read in your browser and never uploaded*.
- **Suggested claims [call]** — *starting points, not facts*, generated deterministically from the schema plus small profiling queries in the browser: `ROWS <t> VALUE >0`; `NULLS <t>.<col> RATE <=5%` for up to 3 columns (fixed conventional threshold, not derived from data, so a suggestion can honestly fail); `VALUES <t>.<col> IN […]` with the distinct values of one text column having ≤ 10; `UNIQUE <t>.<col>` for a column matching `(?i)(^|_)id$`. Each lints clean (tested); inserted only on click.
- **Check** (primary action, beside Lint / Format / Explain SQL): `Checker.acheck` through the bridge. Results: summary bar (count per verdict; `Report.ok` / `Report.verified` explained in words) and a card per claim — verdict badge, measured numbers, reason, collapsible SQL + bound parameters, elapsed ms, diagnostics if invalid. Timeout 30 s: the connector raises `TimeoutError` (→ FL702 ERROR); the worker is restarted, the sample rebuilt, retained CSV `File` handles re-registered, other results marked stale. **Check always runs DuckDB** whatever the Explain dialect selector says; the UI says so. Once data is loaded, Lint uses the `StaticCatalog` too (update "names are not checked" in `playground.md`).
- **Examples**: "Sample shop — an agent's answer" (the landing ledger: pass/fail/no-data) and "Sample shop — data contract" (all pass) become the defaults; each example declares its default action (replaces today's index-based rule in `playground.js`).
- **Loading**: Pyodide and DuckDB start lazily, in parallel, when the playground opens, with a progress status; Lint/Format/Explain enable when Pyodide is ready, Check when both are. CDN unreachable → clear message + `pip install factlang`; never a silent hang.
- **Share link**: encodes claims only, never CSV data. On the sample database it reproduces the same verdicts for date-free claims (the sample's `shipped_at` uses `current_date`; the sample examples are date-free).
- Keyboard: Ctrl/Cmd+Enter runs Check; everything keyboard-reachable; results `aria-live`.

### 5.3 Docs pages
Restyle only (`factlang.css`): type scale, cards/admonitions, code blocks, verdict badges, tables, header/footer consistent with the landing page. Content changes only where the new landing page would otherwise be contradicted. Nav unchanged except the Playground label (e.g. "Playground · live verdicts").

## 6. Verification (machine-checked truth)

- **Glue tests (Python, CI)**: `run` backed by Python `duckdb` (pinned to the WASM engine version) **through the same tagged-JSON marshalling**: for every probe of the two sample examples the rebuilt rows equal Python duckdb's, and outcomes (verdict, measurements, reason) equal `factlang.check` on DuckDB. Plus a marshalling table for DECIMAL, BIGINT, HUGEINT, DOUBLE, DATE, TIMESTAMP, BOOLEAN, VARCHAR, NULL; CSV naming; suggestion generator (lint-clean, deterministic, `paid`/`valid` do not match the id rule); StaticCatalog families; error mapping; timeout → ERROR.
- **Landing facts test**: stat numbers, tier badges and dialect list asserted against code; ledger rows stay re-checked (existing test); sample SQL equals Getting started block 1.
- **Browser e2e (Python Playwright, Chromium via `playwright install --with-deps chromium`, no npm)** **[call]**: a `docs-e2e` job in `docs.yml` gating publish, and in `ci.yml` on pushes touching `docs/`, `site-playground/`, `scripts/docs_hooks.py` or `mkdocs.yml`. Retries CDN failures; if CDNs stay down, publish waits (live site keeps the previous version). It builds and serves the site and: (1) loads the landing page and asserts no Pyodide/DuckDB/wheel request; (2) runs Check on the default example and asserts the expected verdicts; (3) loads fixture CSVs (a quoted-name column; a malformed file), runs a suggested claim, asserts the verdict; (4) asserts no request carries CSV contents; (5) a timeout path (a deliberately slow query with a lowered test timeout) yields ERROR and the page recovers; (6) instant-navigation away and back keeps one worker.
- **Landing budget [call]**: from the e2e network log, the landing page fetches nothing from Pyodide/DuckDB/the wheel/`assets/playground/` beyond `playground.js`/`.css`, and its raw bytes (local server, excluding web fonts) stay under 1.5 MB (today ≈ 0.75 MB).
- `mkdocs build --strict` clean; forbidden-name guard (extended scope) passes; links valid.

## 7. Process (EGM)

Cold-read done (this revision). The plan splits the work into goldfish tasks with disjoint files, each implemented by a fresh agent in a private worktree and reviewed by a fresh reviewer; then one whole-branch review including a visual review from screenshots (desktop/mobile × light/dark); then the owner pushes (agent pushes to `main` are blocked) and the Docs workflow deploys. Expected tasks (the plan decides): T1 visual system + docs restyle; T2 landing page + build-time facts; T3 playground engine (glue check path, marshalling, DuckDB-WASM worker bridge, CSV loading, suggestions); T4 playground UI; T5 tests/CI (facts test, e2e job, guard scope).

## 8. Risks accepted
- Parsing/analysis runs on the UI thread under Pyodide (inline executors): fine at playground scale; the 200-claim cap bounds it.
- Engine skew between CI duckdb and DuckDB-WASM is controlled by pinning; CSV sniffing differences only affect visitor files.
- The e2e job downloads ~10 MB Pyodide + DuckDB wasm per run.

## 9. Left to the builder
Exact copy (within §5 and §2's truthfulness rule), markup, class names, file split inside `site-playground/`, animation details, worker structure, mvp/eh bundle selection.
