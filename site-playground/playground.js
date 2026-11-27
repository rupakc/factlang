/* FactLang playground: the real library in Pyodide, real verdicts from DuckDB-WASM.
 *
 * Loads Pyodide from the official CDN, unpacks the factlang wheel this site was built with
 * (assets/playground/wheel.json names it) and runs glue.py: that is enough for Lint, Format
 * and Explain SQL. Check needs data: engine.js (imported lazily, next to this file) starts
 * DuckDB-WASM in a worker, builds the sample shop and loads the visitor's CSV files, which
 * never leave the browser. Both downloads start when the playground opens, in parallel.
 *
 * This script is on every page of the site (extra_javascript); it does nothing unless the
 * page holds #fl-playground. With Material's instant navigation the page can be left and
 * re-entered without a reload: Pyodide, the engine (one worker) and its tables are kept at
 * module level and the new page attaches to them. */
(function () {
  "use strict";

  // The default examples: glue.py's SAMPLE_EXAMPLES_JSON, verbatim. The page needs them before
  // Python has started; tests/playground/test_playground_page.py keeps the copy equal.
  var SAMPLE_EXAMPLES = [
    {
      "name": "Sample shop — an agent's answer",
      "action": "check",
      "text": "# An agent's answer about the sample shop, written as claims\nJOIN orders.customer_id -> customers.id CARD N:1 MATCH >=99%\nNULLS customers.email RATE <=5%\nVALUES orders.status IN ['paid', 'open', 'shipped']\nCOUNT orders VALUE ==1400 WHERE status = 'paid'\nSUM orders.amount VALUE ==209000 TOLERANCE 1%\nAVG orders.amount VALUE ==100 WHERE status = 'lost'\n"
    },
    {
      "name": "Sample shop — data contract",
      "action": "check",
      "text": "# What the sample shop promises: every line holds\nROWS customers VALUE ==500\nUNIQUE customers.id\nNULLS customers.email RATE <=5%\nUNIQUE orders.id\nTYPE orders.amount IS decimal\nVALUES orders.status IN ['paid', 'open', 'refunded']\nCOUNT orders VALUE ==1400 WHERE status = 'paid'\nRANGE orders.amount BETWEEN [5, 204]\nJOIN orders.customer_id -> customers.id CARD N:1 MATCH >=97%\n"
    }
  ]; // END SAMPLE_EXAMPLES

  // Syntax tours on tables that are not loaded: their default action compiles, it does not run.
  var MORE_EXAMPLES = [
    {
      name: "Namespaces and composite keys",
      action: "explain",
      text: [
        "USE analytics.shop",
        "UNIQUE order_items.order_id WITH [line_no]",
        "JOIN order_items.order_id -> orders.order_id WITH [shop_id] TO [shop_id] MATCH >=99%",
        "FANOUT order_items.order_id MAX_PER_KEY <=50 WHERE deleted IS NULL OR NOT deleted",
        "RANGE orders.placed_on BETWEEN [DATE '2020-01-01', DATE '2030-12-31']",
        ""
      ].join("\n")
    },
    {
      name: "Values with comments",
      action: "explain",
      text: [
        "VALUES opportunities.stage IN ['Prospect', 'Won', 'Lost', 'Churned']",
        "VALUES orders.cancel_reason IN ['other', 'fraud', 'late'] SPLIT ':' ALLOW <=1%",
        "PATTERN customers.email LIKE '%@%.%' RATE >=99%",
        "COUNT opportunities VALUE ==1717 WHERE stage = 'Churned'",
        ""
      ].join("\n")
    },
    {
      name: "Mistakes to fix",
      action: "lint",
      text: [
        "UNIQE orders.id",
        "NULLS customers.email RATE <=120%",
        "COUNT orders WHERE status = 'paid' VALUE ==1400",
        "FANOUT order_items.order_id MAX_PER_KEY >= 3",
        "VALUES orders.status IN ['paid', 'open'",
        "ROWS orders VALUE >= 1 WHERE shipped_at = NULL",
        ""
      ].join("\n")
    }
  ];

  var EXAMPLES = SAMPLE_EXAMPLES.concat(MORE_EXAMPLES);
  var VERDICTS = ["pass", "fail", "no_data", "error", "unprobeable", "invalid"];
  var CSV_NAME = /\.csv$/i;

  var SQL_KEYWORDS = /\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|HAVING|LEFT JOIN|INNER JOIN|CROSS JOIN|JOIN|ON|AS|AND|OR|NOT|IN|IS|NULL|CASE|WHEN|THEN|ELSE|END|UNION ALL|LIMIT|TOP|FETCH FIRST|ROWS ONLY|CAST|COALESCE|DISTINCT|COUNT|COUNT_BIG|COUNTIF|COUNT_IF|SUM|AVG|MIN|MAX|FILTER|LOWER|TRIM|LIKE|WITH)\b/g;

  function esc(text) {
    return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function highlightSql(sql) {
    return esc(sql)
      .replace(/'(?:[^']|'')*'/g, function (s) { return '<span class="fl-tok-str">' + s + "</span>"; })
      .replace(SQL_KEYWORDS, '<span class="fl-tok-kw">$1</span>');
  }

  function highlightClaims(text) {
    return window.FactLangHighlight ? window.FactLangHighlight(text) : esc(text);
  }

  function verdictLabel(v) {
    return String(v).replace("_", " ");
  }

  function plural(n, word) {
    return n + " " + word + (n === 1 ? "" : "s");
  }

  function messageOf(err) {
    return String((err && err.message) || err).replace(/^Error:\s*/, "");
  }

  function baseUrl() {
    var script = document.querySelector('script[src*="assets/playground/playground.js"]');
    return script ? script.src.replace(/playground\.js(\?.*)?$/, "") : "../assets/playground/";
  }

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src;
      s.onload = resolve;
      s.onerror = function () { reject(new Error("could not load " + src)); };
      document.head.appendChild(s);
    });
  }

  // -- shared across page visits (instant navigation keeps this script alive) -----------------

  var runtime = null; // a Promise of the ready Pyodide instance
  var engineModule = null; // a Promise of engine.js
  var shared = {
    engine: null, // the ready Engine, once started
    inflight: null, // the label of the running Check / CSV load / Reset (one at a time)
    suggestions: {}, // table name -> suggested claims
    text: null, // the editor text, kept across page visits
    example: "0", // the chosen example, ditto
    mounts: [] // live page views, refreshed when `inflight` changes
  };

  function startPython(root, status) {
    if (runtime) return runtime;
    var cdn = root.getAttribute("data-pyodide");
    var base = baseUrl();
    runtime = (window.loadPyodide ? Promise.resolve() : loadScript(cdn + "pyodide.js"))
      .then(function () {
        status("Starting Python in your browser…");
        return window.loadPyodide({ indexURL: cdn });
      })
      .then(function (pyodide) {
        return fetch(base + "wheel.json")
          .then(function (r) { return r.json(); })
          .then(function (manifest) {
            if (!manifest.wheel) throw new Error("this build of the site has no factlang wheel");
            status("Installing " + manifest.wheel + "…");
            return fetch(base + manifest.wheel);
          })
          .then(function (r) {
            if (!r.ok) throw new Error("could not download the factlang wheel (" + r.status + ")");
            return r.arrayBuffer();
          })
          .then(function (buffer) {
            pyodide.unpackArchive(buffer, "wheel");
            return fetch(base + "glue.py");
          })
          .then(function (r) {
            if (!r.ok) throw new Error("could not download the playground glue (" + r.status + ")");
            return r.text();
          })
          .then(function (glue) {
            pyodide.runPython(glue);
            return pyodide;
          });
      });
    runtime.catch(function () { runtime = null; });
    return runtime;
  }

  function loadEngineModule() {
    if (!engineModule) {
      engineModule = import(baseUrl() + "engine.js");
      engineModule.catch(function () { engineModule = null; });
    }
    return engineModule;
  }

  function notify() {
    shared.mounts = shared.mounts.filter(function (m) { return document.body.contains(m.root); });
    shared.mounts.forEach(function (m) { m.refresh(); });
  }

  // Run `fn` (returning a Promise) as the one database operation in flight; null if busy.
  function exclusive(label, fn) {
    if (shared.inflight || !shared.engine) return Promise.resolve(null);
    shared.inflight = label;
    notify();
    var p;
    try { p = Promise.resolve(fn()); } catch (err) { p = Promise.reject(err); }
    return p.finally(function () {
      shared.inflight = null;
      notify();
    });
  }

  // -- rendering --------------------------------------------------------------------------------

  function renderLint(data) {
    if (!data.diagnostics.length) {
      return '<p class="fl-pg__ok"><span class="fl-verdict fl-verdict--pass">clean</span> ' +
        plural(data.claims, "claim") + ", no diagnostics.</p>";
    }
    var counts = {};
    data.diagnostics.forEach(function (d) { counts[d.severity] = (counts[d.severity] || 0) + 1; });
    var summary = Object.keys(counts).map(function (k) { return plural(counts[k], k); }).join(", ");
    var rendered = esc(data.rendered)
      .replace(/^(error)(\[FL\d{3}\])/gm, '<span class="fl-pg__sev fl-pg__sev--error">$1$2</span>')
      .replace(/^(warning)(\[FL\d{3}\])/gm, '<span class="fl-pg__sev fl-pg__sev--warning">$1$2</span>')
      .replace(/^(info)(\[FL\d{3}\])/gm, '<span class="fl-pg__sev fl-pg__sev--info">$1$2</span>')
      .replace(/^(\s*\^+)$/gm, '<span class="fl-pg__caret">$1</span>')
      .replace(/^(did you mean: .*|help: .*)$/gm, '<span class="fl-pg__help">$1</span>');
    return '<p class="fl-pg__summary">' + esc(summary) + " · codes: " + codeLinks(data.diagnostics) + "</p>" +
      '<pre class="fl-pg__diag">' + rendered + "</pre>";
  }

  function codeLinks(diagnostics) {
    return diagnostics.map(function (d) { return d.code; })
      .filter(function (c, i, a) { return c && a.indexOf(c) === i; })
      .map(function (c) { return '<a href="../reference/diagnostics/#' + esc(c.toLowerCase()) + '">' + esc(c) + "</a>"; }).join(" ");
  }

  function renderFormat(data) {
    if (data.error) {
      return '<p class="fl-pg__summary">The text has syntax errors, so it cannot be formatted without losing lines. Lint it to see them.</p>' +
        '<pre class="fl-pg__diag">' + esc(data.error) + "</pre>";
    }
    return '<p class="fl-pg__summary">Canonical form. <button type="button" class="fl-pg__link" data-apply>Replace the editor text</button></p>' +
      '<pre class="fl-pg__code"><code>' + highlightClaims(data.text) + "</code></pre>";
  }

  function paramsTable(params) {
    return '<table class="fl-pg__params"><thead><tr><th>name</th><th>value</th><th>type family</th></tr></thead><tbody>' +
      params.map(function (p) {
        return "<tr><td>" + esc(p[0]) + "</td><td><code>" + esc(typeof p[1] === "string" ? p[1] : JSON.stringify(p[1])) + "</code></td><td>" + esc(p[2] || "") + "</td></tr>";
      }).join("") + "</tbody></table>";
  }

  function renderExplain(data) {
    if (!data.claims.length) return '<p class="fl-pg__summary">No claims to explain.</p>';
    return data.claims.map(function (c) {
      var head = '<div class="fl-pg__claim"><code>' + highlightClaims(c.claim) + "</code></div>";
      if (c.kind === "outcome") {
        return '<section class="fl-pg__probe">' + head + '<p><span class="fl-verdict fl-verdict--' + esc(c.verdict) + '">' +
          esc(verdictLabel(c.verdict)) + "</span> " + esc(c.explanation) + "</p></section>";
      }
      var params = c.params.length
        ? '<details><summary>Executed form: template and ' + plural(c.params.length, "bind parameter") + "</summary>" +
          '<pre class="fl-pg__code"><code>' + highlightSql(c.template) + "</code></pre>" + paramsTable(c.params) + "</details>"
        : "";
      return '<section class="fl-pg__probe">' + head +
        '<p class="fl-pg__meta">' + esc(data.dialect) + ', tier <a href="../guide/evidence/#tiers">' + esc(c.tier) + "</a></p>" +
        '<pre class="fl-pg__code"><code>' + highlightSql(c.sql) + "</code></pre>" + params + "</section>";
    }).join("");
  }

  function factValue(value) {
    if (value === null || value === undefined) return '<span class="fl-pg__null">null</span>';
    if (typeof value === "object") return "<code>" + esc(JSON.stringify(value)) + "</code>";
    return "<code>" + esc(String(value)) + "</code>";
  }

  function renderDiagnostics(list) {
    if (!list || !list.length) return "";
    return '<ul class="fl-pg__diags">' + list.map(function (d) {
      var where = d.span ? " (line " + d.span.line + ", column " + d.span.col + ")" : "";
      var extra = d.suggestion ? '<span class="fl-pg__help"> did you mean: ' + esc(d.suggestion) + "</span>"
        : (d.hint ? '<span class="fl-pg__help"> help: ' + esc(d.hint) + "</span>" : "");
      return '<li><span class="fl-pg__sev fl-pg__sev--' + esc(d.severity || "error") + '">' + esc(d.severity || "error") +
        '</span> <a href="../reference/diagnostics/#' + esc(String(d.code || "").toLowerCase()) + '">' + esc(d.code || "") + "</a> " +
        esc(d.message || "") + esc(where) + extra + "</li>";
    }).join("") + "</ul>";
  }

  function renderCard(o) {
    var v = o.verdict;
    var facts = o.facts && Object.keys(o.facts).length
      ? '<dl class="fl-pg__facts">' + Object.keys(o.facts).map(function (k) {
          return "<div><dt>" + esc(k) + "</dt><dd>" + factValue(o.facts[k]) + "</dd></div>";
        }).join("") + "</dl>"
      : "";
    var ev = o.evidence || {};
    var sql = o.sql
      ? '<details class="fl-pg__evidence"><summary>SQL' + (o.params && o.params.length ? " and " + plural(o.params.length, "bound parameter") : "") + "</summary>" +
        '<pre class="fl-pg__code"><code>' + highlightSql(o.sql) + "</code></pre>" +
        (o.params && o.params.length ? paramsTable(o.params) : "") + "</details>"
      : "";
    var meta = [];
    if (o.elapsed_ms !== null && o.elapsed_ms !== undefined) meta.push(o.elapsed_ms + " ms");
    if (ev.tier) meta.push("tier " + ev.tier);
    if (ev.fused_with && ev.fused_with.length) meta.push("one query with " + plural(ev.fused_with.length, "other claim"));
    if (ev.method && ev.method !== "exact") meta.push(ev.method);
    return '<article class="fl-pg__card fl-pg__card--' + esc(v) + '">' +
      '<header class="fl-pg__card-head"><span class="fl-verdict fl-verdict--' + esc(v) + '">' + esc(verdictLabel(v)) + "</span>" +
      '<code class="fl-pg__card-claim">' + highlightClaims(o.claim_text) + "</code></header>" +
      (o.explanation ? '<p class="fl-pg__why">' + esc(o.explanation) + "</p>" : "") +
      facts + renderDiagnostics(o.diagnostics) + sql +
      (meta.length ? '<p class="fl-pg__meta">' + esc(meta.join(" · ")) + "</p>" : "") +
      "</article>";
  }

  function okInWords(data) {
    var counts = data.counts || {};
    var parts = [];
    if (counts.fail) parts.push(counts.fail + " failed");
    if (counts.error) parts.push(counts.error + " could not be measured");
    if (counts.invalid) parts.push(counts.invalid + " " + (counts.invalid === 1 ? "is" : "are") + " invalid");
    if (counts.no_data) parts.push(counts.no_data + " had no data to decide on");
    if (counts.unprobeable) parts.push(counts.unprobeable + " cannot be measured by design");
    var detail = parts.length ? parts.join(", ") : "";
    if (data.verified) {
      return "<strong>Verified.</strong> Every claim that can be measured was measured and passed " +
        "(<code>Report.verified</code> and <code>Report.ok</code> are both true).";
    }
    if (data.ok) {
      return "<strong>OK, not verified.</strong> Nothing failed and nothing errored, but " +
        esc(detail || "nothing was measured") +
        " (<code>Report.ok</code> is true, <code>Report.verified</code> is false).";
    }
    return "<strong>Not OK.</strong> " +
      esc(detail || "the claims have an error that is not a claim of its own") +
      " (<code>Report.ok</code> and <code>Report.verified</code> are false).";
  }

  function renderCheck(data, ctx) {
    if (data.error) {
      return '<p class="fl-pg__summary fl-pg__status--error">The check itself failed: ' + esc(data.error) + "</p>";
    }
    var counts = data.counts || {};
    var badges = VERDICTS.filter(function (v) { return counts[v]; }).map(function (v) {
      return '<span class="fl-verdict fl-verdict--' + v + '">' + counts[v] + " " + esc(verdictLabel(v)) + "</span>";
    }).join(" ");
    var outcomes = data.outcomes || [];
    var queries = {};
    outcomes.forEach(function (o) { if (o.sql && o.elapsed_ms !== null && o.elapsed_ms !== undefined) queries[o.sql] = o.elapsed_ms; });
    var sqls = Object.keys(queries);
    var total = sqls.reduce(function (n, k) { return n + queries[k]; }, 0);
    var where = "Checked on DuckDB " + (ctx.version || "") + " in your browser, " +
      (sqls.length ? sqls.length + (sqls.length === 1 ? " query" : " queries") + " in " + total + " ms" : "no query run") + ".";
    if (ctx.dialect && ctx.dialect !== "duckdb") where += " The dialect menu (" + ctx.dialect + ") applies to Explain SQL only.";
    var restarted = data.restarted
      ? '<p class="fl-pg__notice" role="note">The database was restarted during this check (a query ran past ' + esc(data.timeoutS) +
        " s, or the worker failed). Results shown before this check may be stale; run them again.</p>"
      : "";
    return '<div class="fl-pg__verdicts">' +
      '<div class="fl-pg__summary-bar">' + (badges || '<span class="fl-pg__muted">no claims</span>') + "</div>" +
      '<p class="fl-pg__ok-words">' + okInWords(data) + "</p>" +
      '<p class="fl-pg__meta">' + esc(where) + "</p>" + restarted +
      (data.diagnostics && data.diagnostics.length ? renderDiagnostics(data.diagnostics) : "") +
      outcomes.map(renderCard).join("") + "</div>";
  }

  function encodeShare(text) {
    return btoa(unescape(encodeURIComponent(text))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  }

  function decodeShare(token) {
    try {
      var b64 = token.replace(/-/g, "+").replace(/_/g, "/");
      return decodeURIComponent(escape(atob(b64)));
    } catch (e) {
      return null;
    }
  }

  // -- one page view ----------------------------------------------------------------------------

  function mount(root) {
    if (root.dataset.flMounted) return;
    root.dataset.flMounted = "1";

    var source = root.querySelector("#fl-pg-source");
    var hl = root.querySelector(".fl-pg__hl code");
    var hlPre = root.querySelector(".fl-pg__hl");
    var dialect = root.querySelector("#fl-pg-dialect");
    var example = root.querySelector("#fl-pg-example");
    var result = root.querySelector("#fl-pg-result");
    var statusEl = root.querySelector("#fl-pg-status");
    var buttons = root.querySelectorAll("[data-action]");
    var checkButton = root.querySelector('[data-action="check"]');
    var engineLabel = root.querySelector("#fl-pg-engine");
    var engineStatusEl = root.querySelector("#fl-pg-engine-status");
    var tablesEl = root.querySelector("#fl-pg-tables");
    var loadButton = root.querySelector("#fl-pg-load");
    var resetButton = root.querySelector("#fl-pg-reset");
    var fileInput = root.querySelector("#fl-pg-csv");
    var drop = root.querySelector("#fl-pg-data");
    var csvErrors = root.querySelector("#fl-pg-csv-errors");
    var suggestBox = root.querySelector("#fl-pg-suggest");
    var suggestList = root.querySelector("#fl-pg-suggest-list");
    var dataToggle = root.querySelector("#fl-pg-data-toggle");

    var lastAction = null;
    var lintTimer = null;
    var py = null;
    var engine = null;
    var engineFailed = null;
    var wantCheck = false; // run Check as soon as the engine is ready and idle
    var checkedText = null; // the text the shown verdicts belong to
    var view = { root: root, refresh: refresh };
    shared.mounts.push(view);

    function status(text, isError) {
      statusEl.textContent = text || "";
      statusEl.hidden = !text;
      statusEl.classList.toggle("fl-pg__status--error", !!isError);
    }

    function engineStatus(text, isError) {
      engineStatusEl.textContent = text || "";
      engineStatusEl.hidden = !text;
      engineStatusEl.classList.toggle("fl-pg__status--error", !!isError);
    }

    function paint() {
      hl.innerHTML = highlightClaims(source.value) + "\n";
      hlPre.scrollTop = source.scrollTop;
      hlPre.scrollLeft = source.scrollLeft;
    }

    function pressed(action) {
      buttons.forEach(function (b) { b.setAttribute("aria-pressed", b.dataset.action === action ? "true" : "false"); });
    }

    // Enable each control for what is ready; nothing that touches the database while a Check,
    // a CSV load or a Reset is running (the engine serialises queries, not whole operations).
    function alive() {
      return document.body.contains(root);
    }

    function refresh() {
      if (!alive()) {
        // a page left by instant navigation: never run a check or touch the engine again
        wantCheck = false;
        shared.mounts = shared.mounts.filter(function (m) { return m !== view; });
        return;
      }
      var busy = !!shared.inflight;
      buttons.forEach(function (b) { if (b !== checkButton) b.disabled = !py; });
      checkButton.disabled = !engine || busy;
      checkButton.textContent = busy && shared.inflight === "check" ? "Checking…" : "Check";
      loadButton.disabled = !engine || busy;
      resetButton.disabled = !engine || busy || !hasCsv();
      drop.classList.toggle("is-busy", busy);
      root.querySelectorAll("[data-suggest], [data-claim]").forEach(function (b) { b.disabled = !engine || (busy && b.hasAttribute("data-suggest")); });
      root.setAttribute("aria-busy", busy ? "true" : "false");
      if (!busy && engine && wantCheck) {
        wantCheck = false;
        runCheck();
      }
    }

    function hasCsv() {
      return !!engine && engine.tables().some(function (t) { return t.source === "csv"; });
    }

    EXAMPLES.forEach(function (ex, i) {
      var opt = document.createElement("option");
      opt.value = String(i);
      opt.textContent = ex.name;
      example.appendChild(opt);
    });

    try {
      var savedDialect = window.localStorage.getItem("factlang.playground.dialect");
      if (savedDialect) dialect.value = savedDialect;
    } catch (e) { /* storage may be unavailable */ }

    // a share link wins; then the text left on an earlier visit; then the first example
    var linked = /^#claims=/.test(location.hash) ? decodeShare(location.hash.slice(8)) : null;
    var initialAction;
    if (linked !== null) {
      source.value = linked;
      initialAction = "check";
    } else if (shared.text !== null) {
      source.value = shared.text;
      example.value = shared.example;
      var same = EXAMPLES.filter(function (ex) { return ex.text === shared.text; })[0];
      initialAction = same ? same.action : "lint";
    } else {
      source.value = EXAMPLES[0].text;
      initialAction = EXAMPLES[0].action;
    }
    paint();

    function remember() {
      shared.text = source.value;
      shared.example = example.value;
    }

    function markStale() {
      if (lastAction !== "check" || checkedText === null || source.value === checkedText) return;
      if (result.querySelector(".fl-pg__stale")) return;
      var note = document.createElement("p");
      note.className = "fl-pg__stale";
      note.setAttribute("role", "note");
      note.textContent = "The claims have changed since this check. Press Check (Ctrl/⌘ + Enter) to run them again.";
      result.insertBefore(note, result.firstChild);
    }

    function lintNow() {
      var text = source.value;
      if (engine) {
        return engine.lint(text).then(function (data) {
          if (source.value === text && lastAction === "lint") result.innerHTML = renderLint(data);
        });
      }
      result.innerHTML = renderLint(JSON.parse(py.globals.get("fl_lint")(text)));
      return Promise.resolve();
    }

    function run(action) {
      if (!py || !alive()) return;
      if (action === "check") { runCheck(); return; }
      lastAction = action;
      pressed(action);
      var text = source.value;
      try {
        var data;
        if (action === "lint") {
          lintNow().catch(showError);
        } else if (action === "format") {
          data = JSON.parse(py.globals.get("fl_format")(text));
          result.innerHTML = renderFormat(data);
          var apply = result.querySelector("[data-apply]");
          if (apply) apply.addEventListener("click", function () { source.value = data.text; paint(); remember(); });
        } else {
          data = JSON.parse(py.globals.get("fl_explain")(text, dialect.value));
          result.innerHTML = renderExplain(data);
        }
      } catch (err) {
        showError(err);
      }
    }

    function showError(err) {
      result.innerHTML = '<p class="fl-pg__summary fl-pg__status--error">' + esc(messageOf(err)) + "</p>";
    }

    function runCheck() {
      if (!alive()) return;
      // a Check is already running: the button says "Checking…"; a second one is not queued
      if (shared.inflight === "check") return;
      if (!engine || shared.inflight) {
        // not ready yet: lint now, check the moment the database is free
        wantCheck = true;
        if (py && !shared.inflight) {
          lastAction = "lint";
          pressed("check");
          lintNow().then(function () {
            if (!engine && !engineFailed) {
              result.insertAdjacentHTML("afterbegin", '<p class="fl-pg__notice" role="note">Check runs as soon as DuckDB has started; meanwhile, this is Lint.</p>');
            }
          }).catch(showError);
        }
        return;
      }
      wantCheck = false;
      var text = source.value;
      var claims = text.split("\n").filter(function (l) { return l.trim() && l.trim().charAt(0) !== "#"; }).length;
      lastAction = "check";
      pressed("check");
      status("");
      result.innerHTML = '<p class="fl-pg__status">Checking ' + plural(claims, "line") + " on DuckDB…</p>";
      var ctx = { version: engine.version, dialect: dialect.value };
      exclusive("check", function () {
        return engine.check(text).then(function (data) {
          checkedText = text;
          // the visitor asked for Lint / Format / Explain meanwhile: keep what they asked for
          if (lastAction !== "check") return;
          result.innerHTML = renderCheck(data, ctx);
          markStale();
        });
      }).catch(function (err) {
        if (lastAction === "check") showError("Check could not run: " + messageOf(err));
      });
    }

    // -- data panel -----------------------------------------------------------------------------

    function renderTables() {
      if (!engine) return;
      var tables = engine.tables();
      tablesEl.innerHTML = tables.map(function (t) {
        var cols = t.columns.map(function (c) {
          var spelled = c.spelling
            ? "<code>" + esc(c.spelling) + "</code>"
            : '<span class="fl-pg__muted" title="A line break or NUL in the name: FactLang cannot write it">not addressable</span>';
          return "<tr><td>" + esc(c.name) + "</td><td>" + esc(c.type) + "</td><td>" + spelled + "</td></tr>";
        }).join("");
        var kind = t.source === "csv" ? '<span class="fl-badge fl-badge--teal">CSV</span>' : '<span class="fl-badge fl-badge--indigo">sample</span>';
        var spelled = t.spelling && t.spelling !== t.name ? ' <span class="fl-pg__muted">as <code>' + esc(t.spelling) + "</code></span>" : "";
        return '<details class="fl-pg__table" data-table="' + esc(t.name) + '">' +
          '<summary><span class="fl-pg__tname">' + esc(t.name) + "</span>" + spelled + " " + kind +
          '<span class="fl-pg__rows">' + Number(t.rows).toLocaleString("en-US") + (t.rows === 1 ? " row" : " rows") + "</span></summary>" +
          '<div class="fl-pg__cols-wrap"><table class="fl-pg__cols"><thead><tr><th>Column</th><th>DuckDB type</th><th>In a claim</th></tr></thead><tbody>' +
          cols + "</tbody></table></div>" +
          '<button type="button" class="fl-pg__link" data-suggest="' + esc(t.name) + '">Suggest claims about ' + esc(t.name) + "</button>" +
          "</details>";
      }).join("");
      labelDataToggle(tables.length);
      // keep only suggestions for tables that are still loaded
      var names = tables.map(function (t) { return t.name; });
      Object.keys(shared.suggestions).forEach(function (k) { if (names.indexOf(k) < 0) delete shared.suggestions[k]; });
      renderSuggestions();
      refresh();
    }

    // Phones show the table list collapsed (the toggle is hidden on wider screens).
    function labelDataToggle(n) {
      if (!dataToggle) return;
      var open = drop.classList.contains("is-expanded");
      dataToggle.setAttribute("aria-expanded", open ? "true" : "false");
      dataToggle.textContent = open ? "Hide tables" : "Show tables" + (n ? " (" + n + ")" : "");
    }

    // Bring the suggestions for a table just loaded into view, and mark them for a moment.
    function showSuggestions() {
      if (suggestBox.hidden) return;
      var still = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      try { suggestBox.scrollIntoView({ block: "nearest", behavior: still ? "auto" : "smooth" }); } catch (e) { suggestBox.scrollIntoView(false); }
      suggestBox.classList.add("is-fresh");
      window.setTimeout(function () { suggestBox.classList.remove("is-fresh"); }, 2500);
    }

    function renderSuggestions() {
      var names = Object.keys(shared.suggestions);
      suggestBox.hidden = !names.length;
      suggestList.innerHTML = names.map(function (name) {
        var claims = shared.suggestions[name];
        return '<div class="fl-pg__sgroup"><h4>' + esc(name) + "</h4>" +
          (claims.length
            ? "<ul>" + claims.map(function (c) {
                return '<li><button type="button" class="fl-pg__sugg" data-claim="' + esc(c) + '" title="Add this claim to the editor"><code>' +
                  highlightClaims(c) + "</code></button></li>";
              }).join("") + "</ul>"
            : '<p class="fl-pg__hint">No suggestion fits this table.</p>') + "</div>";
      }).join("");
      refresh();
    }

    function suggestFor(names) {
      var out = Promise.resolve();
      names.forEach(function (name) {
        out = out.then(function () {
          return engine.suggest(name).then(function (claims) { shared.suggestions[name] = claims; }, function () { /* no suggestions */ });
        });
      });
      return out;
    }

    function insertClaim(claim) {
      var text = source.value;
      if (text && !/\n$/.test(text)) text += "\n";
      source.value = text + claim + "\n";
      paint();
      source.scrollTop = source.scrollHeight;
      onEdit();
    }

    function renderCsvErrors(errors) {
      csvErrors.innerHTML = errors.map(function (e) {
        var message = String(e.message || "");
        var first = message.split("\n")[0];
        var short = first.length > 240 ? first.slice(0, 240) + "…" : first;
        var more = message !== short
          ? '<details><summary>DuckDB\'s full message</summary><pre class="fl-pg__diag">' + esc(message) + "</pre></details>"
          : "";
        return '<div class="fl-pg__csv-error" role="alert"><strong>' + esc(e.file) + "</strong> was not loaded: " + esc(short) + more + "</div>";
      }).join("");
    }

    function loadFiles(fileList) {
      var files = Array.prototype.slice.call(fileList || []);
      if (!files.length) return;
      if (!engine) { engineStatus("The database has not started yet; load the files once it has.", true); return; }
      if (shared.inflight) { engineStatus("Wait for the running " + shared.inflight + " to finish, then load the files.", true); return; }
      var rejected = files.filter(function (f) { return !CSV_NAME.test(f.name); }).map(function (f) {
        return { file: f.name, message: "Only .csv files can be loaded." };
      });
      var csvFiles = files.filter(function (f) { return CSV_NAME.test(f.name); });
      renderCsvErrors(rejected);
      if (!csvFiles.length) return;
      engineStatus("Loading " + plural(csvFiles.length, "file") + " into DuckDB…");
      exclusive("CSV load", function () {
        return engine.loadCsv(csvFiles).then(function (out) {
          renderCsvErrors(rejected.concat(out.errors));
          var names = out.tables.map(function (t) { return t.name; });
          return suggestFor(names).then(function () {
            renderTables();
            out.tables.forEach(function (t) {
              var details = tablesEl.querySelector('[data-table="' + (window.CSS && CSS.escape ? CSS.escape(t.name) : t.name) + '"]');
              if (details) details.open = true;
            });
            engineStatus(out.tables.length
              ? "Loaded " + out.tables.map(function (t) { return t.name + " (" + plural(t.rows, "row") + ")"; }).join(", ") + "."
              : "");
            if (lastAction === "check") markStaleData();
            if (names.length) showSuggestions();
          });
        });
      }).catch(function (err) {
        engineStatus("Loading failed: " + messageOf(err), true);
        renderTables();
      });
    }

    function markStaleData() {
      if (!result.querySelector(".fl-pg__verdicts") || result.querySelector(".fl-pg__stale")) return;
      var note = document.createElement("p");
      note.className = "fl-pg__stale";
      note.setAttribute("role", "note");
      note.textContent = "The loaded tables have changed since this check. Press Check to run it again.";
      result.insertBefore(note, result.firstChild);
    }

    function reset() {
      exclusive("Reset", function () {
        engineStatus("Removing the CSV tables…");
        return engine.reset().then(function () {
          renderCsvErrors([]);
          renderTables();
          engineStatus("The CSV tables were removed; the sample shop is loaded.");
          markStaleData();
        });
      }).catch(function (err) {
        engineStatus("Reset failed: " + messageOf(err), true);
        renderTables();
      });
    }

    // -- events ---------------------------------------------------------------------------------

    function onEdit() {
      paint();
      remember();
      if (!py) return;
      window.clearTimeout(lintTimer);
      if (lastAction === "check") { markStale(); return; }
      lintTimer = window.setTimeout(function () { run(lastAction === "explain" ? "explain" : "lint"); }, 350);
    }

    source.addEventListener("input", onEdit);
    source.addEventListener("scroll", paint);
    root.addEventListener("keydown", function (ev) {
      if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") {
        ev.preventDefault();
        runCheck();
      }
    });
    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        if (b.dataset.action !== "check") wantCheck = false; // the visitor chose another action
        run(b.dataset.action);
      });
    });
    dialect.addEventListener("change", function () {
      try { window.localStorage.setItem("factlang.playground.dialect", dialect.value); } catch (e) { /* ignore */ }
      if (lastAction === "explain") run("explain");
    });
    example.addEventListener("change", function () {
      var ex = EXAMPLES[Number(example.value)];
      source.value = ex.text;
      paint();
      remember();
      checkedText = null;
      wantCheck = false;
      run(ex.action);
    });
    root.querySelector("#fl-pg-share").addEventListener("click", function (ev) {
      var url = location.href.split("#")[0] + "#claims=" + encodeShare(source.value);
      history.replaceState(null, "", url);
      var button = ev.currentTarget;
      var done = function () { button.textContent = "Link copied"; window.setTimeout(function () { button.textContent = "Copy a link to these claims"; }, 1800); };
      if (navigator.clipboard) navigator.clipboard.writeText(url).then(done, done); else done();
    });
    loadButton.addEventListener("click", function () { fileInput.click(); });
    fileInput.addEventListener("change", function () {
      var files = fileInput.files;
      loadFiles(files);
      fileInput.value = "";
    });
    resetButton.addEventListener("click", reset);
    if (dataToggle) {
      dataToggle.addEventListener("click", function () {
        drop.classList.toggle("is-expanded");
        labelDataToggle(engine ? engine.tables().length : 0);
      });
    }
    root.addEventListener("click", function (ev) {
      var target = ev.target.closest ? ev.target.closest("[data-suggest], [data-claim]") : null;
      if (!target || target.disabled || !root.contains(target)) return;
      if (target.hasAttribute("data-claim")) { insertClaim(target.getAttribute("data-claim")); return; }
      var name = target.getAttribute("data-suggest");
      exclusive("suggestion", function () {
        return suggestFor([name]).then(renderSuggestions);
      });
    });
    // Drops are always taken here (never let the browser open the file instead).
    ["dragenter", "dragover"].forEach(function (type) {
      drop.addEventListener(type, function (ev) {
        if (!ev.dataTransfer || Array.prototype.indexOf.call(ev.dataTransfer.types || [], "Files") < 0) return;
        ev.preventDefault();
        ev.dataTransfer.dropEffect = engine && !shared.inflight ? "copy" : "none";
        drop.classList.add("is-dragover");
      });
    });
    drop.addEventListener("dragleave", function (ev) {
      if (!drop.contains(ev.relatedTarget)) drop.classList.remove("is-dragover");
    });
    drop.addEventListener("drop", function (ev) {
      if (!ev.dataTransfer || !ev.dataTransfer.files || !ev.dataTransfer.files.length) return;
      ev.preventDefault();
      drop.classList.remove("is-dragover");
      loadFiles(ev.dataTransfer.files);
    });

    function fillDialects(names) {
      // the static options in playground.md are the fallback; Python knows the real list
      if (!names || !names.length) return;
      var current = dialect.value;
      dialect.innerHTML = "";
      names.forEach(function (name) {
        var opt = document.createElement("option");
        opt.textContent = name;
        dialect.appendChild(opt);
      });
      dialect.value = names.indexOf(current) >= 0 ? current : (names.indexOf("duckdb") >= 0 ? "duckdb" : names[0]);
    }

    // -- start-up -------------------------------------------------------------------------------

    function startDatabase() {
      engineFailed = null;
      engineLabel.textContent = "DuckDB starting";
      engineLabel.classList.add("is-loading");
      engineStatus(shared.engine ? "" : "Starting DuckDB in your browser…");
      loadEngineModule().then(function (m) {
        return m.startEngine({ pyodide: py, onStatus: function (text) { engineStatus(text); } });
      }).then(function (ready) {
        shared.engine = ready;
        engine = ready;
        engineLabel.classList.remove("is-loading", "is-failed");
        engineLabel.classList.add("is-ready");
        engineLabel.textContent = "DuckDB " + ready.version + " · in your browser";
        var icu = ready.icu;
        engineStatus(icu && !icu.loaded
          ? "Time zones (DuckDB's ICU extension) could not be loaded, so FRESH and other claims about \"now\" come back as ERROR."
          : (shared.inflight ? "A " + shared.inflight + " is running…" : ""));
        renderTables();
        refresh();
      }).catch(function (err) {
        engineFailed = err;
        engineLabel.classList.remove("is-loading");
        engineLabel.classList.add("is-failed");
        engineLabel.textContent = "DuckDB unavailable";
        engineStatus("");
        engineStatusEl.hidden = false;
        engineStatusEl.classList.add("fl-pg__status--error");
        engineStatusEl.innerHTML = "DuckDB could not start: " + esc(messageOf(err)) +
          ". Lint, Format and Explain SQL still work; Check needs DuckDB. " +
          '<button type="button" class="fl-pg__link" data-retry>Try again</button> or run FactLang locally with <code>pip install factlang</code>.';
        var retry = engineStatusEl.querySelector("[data-retry]");
        if (retry) retry.addEventListener("click", startDatabase);
        // a pending Check stays pending: "Try again" runs it once DuckDB is up
        refresh();
      });
    }

    // start the DuckDB download now, in parallel with Python (startEngine reuses it)
    loadEngineModule().then(function (m) { if (m.preloadDuckdb) m.preloadDuckdb().catch(function () { /* reported by startEngine */ }); })
      .catch(function () { /* reported by startDatabase */ });

    refresh();
    startPython(root, status).then(function (pyodide) {
      py = pyodide;
      try { fillDialects(JSON.parse(py.globals.get("FL_DIALECTS"))); } catch (e) { /* keep the static list */ }
      status("");
      root.classList.add("fl-pg--ready");
      refresh();
      if (initialAction === "check") runCheck(); else run(initialAction);
      startDatabase();
    }).catch(function (err) {
      status("The playground could not start: " + messageOf(err) + ". Reload to try again, or run FactLang locally with pip install factlang.", true);
      engineLabel.textContent = "DuckDB not started";
      engineStatus("Check needs Python, which could not start.", true);
    });
  }

  function init() {
    var root = document.getElementById("fl-playground");
    if (root) mount(root);
  }

  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(init);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
