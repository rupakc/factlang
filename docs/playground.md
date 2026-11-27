---
title: Playground
hide:
  - navigation
  - toc
---

# Playground

Write claims and check them for real: each one gets a verdict, the numbers that decided it and
the exact SQL that measured them. The claims run on a sample shop database, or on your own CSV
files. This is the real FactLang library, the wheel built from this site's commit, running in
your browser through [Pyodide](https://pyodide.org). The SQL runs in
[DuckDB-WASM](https://duckdb.org/docs/api/wasm/overview), also in your browser. Nothing is sent
to a server.

<div id="fl-playground" class="fl-pg" data-pyodide="https://cdn.jsdelivr.net/pyodide/v0.29.0/full/">
  <div class="fl-pg__bar" role="toolbar" aria-label="Playground actions">
    <label class="fl-pg__field">
      <span>Example</span>
      <select id="fl-pg-example" aria-label="Load an example"></select>
    </label>
    <label class="fl-pg__field">
      <span>Explain dialect</span>
      <select id="fl-pg-dialect" aria-label="SQL dialect for Explain SQL">
        <option>bigquery</option><option>clickhouse</option><option>databricks</option>
        <option selected>duckdb</option><option>mysql</option><option>oracle</option>
        <option>postgres</option><option>redshift</option><option>snowflake</option>
        <option>trino</option><option>tsql</option>
      </select>
    </label>
    <span class="fl-pg__spacer"></span>
    <button type="button" class="fl-pg__btn" data-action="lint" disabled>Lint</button>
    <button type="button" class="fl-pg__btn" data-action="format" disabled>Format</button>
    <button type="button" class="fl-pg__btn" data-action="explain" disabled>Explain SQL</button>
    <button type="button" class="fl-pg__btn fl-pg__btn--primary" data-action="check" disabled>Check</button>
  </div>
  <div class="fl-pg__layout">
    <aside id="fl-pg-data" class="fl-pg__data" aria-labelledby="fl-pg-data-title">
      <div class="fl-pg__data-head">
        <h2 id="fl-pg-data-title" class="fl-pg__data-title">Data</h2>
        <button type="button" class="fl-pg__link fl-pg__data-toggle" id="fl-pg-data-toggle" aria-expanded="false" aria-controls="fl-pg-tables">Show tables</button>
        <span class="fl-pg__engine" id="fl-pg-engine">DuckDB not started</span>
      </div>
      <p class="fl-pg__privacy">Files are read in your browser and never uploaded.</p>
      <div class="fl-pg__engine-status" id="fl-pg-engine-status" role="status" aria-live="polite">Waiting for Python to start…</div>
      <div id="fl-pg-tables" class="fl-pg__tables"></div>
      <div class="fl-pg__drop" id="fl-pg-drop">
        <div class="fl-pg__drop-actions">
          <button type="button" class="fl-pg__btn" id="fl-pg-load" disabled>Load CSV</button>
          <button type="button" class="fl-pg__btn fl-pg__btn--quiet" id="fl-pg-reset" disabled>Reset</button>
        </div>
        <input type="file" id="fl-pg-csv" class="fl-pg__file" accept=".csv" multiple tabindex="-1" aria-hidden="true">
        <p class="fl-pg__hint">or drop CSV files here. Up to 10 files, 100&nbsp;MB each, 200&nbsp;MB in total. Reset removes them and keeps the sample shop.</p>
      </div>
      <div id="fl-pg-csv-errors" class="fl-pg__csv-errors" aria-live="polite"></div>
      <section class="fl-pg__suggest" id="fl-pg-suggest" hidden aria-labelledby="fl-pg-suggest-title">
        <h3 id="fl-pg-suggest-title">Suggested claims</h3>
        <p class="fl-pg__hint">Starting points, not facts: click one to add it to the editor, then Check it.</p>
        <div id="fl-pg-suggest-list"></div>
      </section>
    </aside>
    <div class="fl-pg__panes">
      <div class="fl-pg__editor">
        <div class="fl-pg__hl" aria-hidden="true"><code></code></div>
        <textarea id="fl-pg-source" spellcheck="false" autocapitalize="off" autocomplete="off"
          aria-label="FactLang claims, one per line" wrap="soft"></textarea>
      </div>
      <div class="fl-pg__out" aria-live="polite">
        <div class="fl-pg__status" id="fl-pg-status">Starting Python in your browser. The first visit downloads Python and DuckDB once; later visits are cached.</div>
        <div id="fl-pg-result"></div>
      </div>
    </div>
  </div>
  <div class="fl-pg__foot">
    <span><kbd>Ctrl</kbd>/<kbd class="fl-pg__cmd" title="Command">⌘</kbd> + <kbd>Enter</kbd> runs Check. Check always runs DuckDB, in your browser; the dialect menu changes Explain SQL only.</span>
    <button type="button" class="fl-pg__link" id="fl-pg-share">Copy a link to these claims</button>
  </div>
  <noscript>The playground needs JavaScript. Every example on this site also runs locally: <code>pip install factlang</code>.</noscript>
</div>

## What you can try

- **Check** runs the claims on the tables listed under *Data* and shows a card per claim: its
  [verdict](guide/verdicts.md), the measured numbers, the reason, the SQL with its bound
  parameters and how long it took. Check always runs on DuckDB, whatever the dialect menu says.
  A query gets 30 seconds; past that the claim is ERROR (FL702) and the database is restarted.
- **Lint** parses and analyses the claims against the standard verbs and prints every
  diagnostic with a caret, its code and a suggestion when there is one. Once the database has
  started, table and column names are checked against the loaded tables too.
- **Format** prints the canonical form: the spelling FactLang hashes and stores.
- **Explain SQL** shows each claim's query for the chosen dialect, its bind parameters and
  the dialect's [tier](guide/evidence.md#tiers). Switch dialects to see how `COUNT_BIG`, `TOP`,
  `COUNTIF` or `FILTER (WHERE …)` replace each other while the meaning stays the same.

## The data

The **sample shop** is the database of [Getting started](getting-started.md): 500 `customers`
and 2,000 `orders`. The two *Sample shop* examples run on it; the first is the claim ledger on
the home page.

**Load CSV** (or drop files on the *Data* panel) adds each file as a table next to the sample
tables. The table name comes from the file name: its last extension is dropped, it is
lower-cased, every character other than a letter, a digit or an underscore becomes an
underscore, a name starting with a digit gets the prefix `t_`, and a name already loaded gets
`_2`, `_3`, … appended. So `2024 Sales.v2.csv` becomes `t_2024_sales_v2`. Columns keep their
CSV names; each one is listed with the way a claim writes it. The files are read by DuckDB inside your browser tab and never uploaded,
and a share link carries the claims only, never your data. After loading, the panel offers
suggested claims about the new table: starting points, not facts, so some of them can fail.

To run claims against your own warehouse, install FactLang and follow
[Getting started](getting-started.md).
