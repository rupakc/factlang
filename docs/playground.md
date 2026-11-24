---
title: Playground
hide:
  - navigation
  - toc
---

# Playground

Write claims, then lint them, format them, or see the SQL they compile to for any of the
eleven native dialects. This is the real FactLang library, the wheel built from this site's commit,
running in your browser through [Pyodide](https://pyodide.org). Nothing is sent to a server,
and no warehouse is needed: verdicts need data, so this page stops at the SQL.

<div id="fl-playground" class="fl-pg" data-pyodide="https://cdn.jsdelivr.net/pyodide/v0.29.0/full/">
  <div class="fl-pg__bar" role="toolbar" aria-label="Playground actions">
    <label class="fl-pg__field">
      <span>Example</span>
      <select id="fl-pg-example" aria-label="Load an example"></select>
    </label>
    <label class="fl-pg__field">
      <span>Dialect</span>
      <select id="fl-pg-dialect" aria-label="SQL dialect">
        <option>bigquery</option><option>clickhouse</option><option>databricks</option>
        <option selected>duckdb</option><option>mysql</option><option>oracle</option>
        <option>postgres</option><option>redshift</option><option>snowflake</option>
        <option>trino</option><option>tsql</option>
      </select>
    </label>
    <span class="fl-pg__spacer"></span>
    <button type="button" class="fl-pg__btn" data-action="lint" disabled>Lint</button>
    <button type="button" class="fl-pg__btn" data-action="format" disabled>Format</button>
    <button type="button" class="fl-pg__btn fl-pg__btn--primary" data-action="explain" disabled>Explain SQL</button>
  </div>
  <div class="fl-pg__panes">
    <div class="fl-pg__editor">
      <div class="fl-pg__hl" aria-hidden="true"><code></code></div>
      <textarea id="fl-pg-source" spellcheck="false" autocapitalize="off" autocomplete="off"
        aria-label="FactLang claims, one per line" wrap="off"></textarea>
    </div>
    <div class="fl-pg__out" aria-live="polite">
      <div class="fl-pg__status" id="fl-pg-status">Starting Python in your browser. The first visit downloads about 10 MB; later visits are cached.</div>
      <div id="fl-pg-result"></div>
    </div>
  </div>
  <div class="fl-pg__foot">
    <span><kbd>Ctrl</kbd> + <kbd>Enter</kbd> runs the last action. Lint also runs as you type.</span>
    <button type="button" class="fl-pg__link" id="fl-pg-share">Copy a link to these claims</button>
  </div>
  <noscript>The playground needs JavaScript. Every example on this site also runs locally: <code>pip install factlang</code>.</noscript>
</div>

## What you can try

- **Lint** parses and analyses the claims against the standard verbs, and prints every
  diagnostic with a caret, its code and a suggestion when there is one. There is no catalog
  here, so table and column names are not checked.
- **Format** prints the canonical form: the spelling FactLang hashes and stores.
- **Explain SQL** shows each claim's query for the chosen dialect, its bind parameters and
  the dialect's [tier](guide/evidence.md#tiers). Switch dialects to see how `COUNT_BIG`, `TOP`,
  `COUNTIF` or `FILTER (WHERE …)` replace each other while the meaning stays the same.

To run claims against real data, install FactLang and follow
[Getting started](getting-started.md).
