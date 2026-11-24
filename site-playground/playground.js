/* FactLang playground: the real library, running in Pyodide.
 *
 * Loads Pyodide from the official CDN, unpacks the factlang wheel this site was built with
 * (assets/playground/wheel.json names it) and offers Lint / Format / Explain SQL. Nothing
 * here executes SQL: the core is pure Python and needs no warehouse to compile. */
(function () {
  "use strict";

  var EXAMPLES = [
    {
      name: "Shop health checks",
      text: [
        "# Facts an analyst stated about the shop",
        "JOIN orders.customer_id -> customers.id CARD N:1 MATCH >=99%",
        "NULLS customers.email RATE <=5%",
        "VALUES orders.status IN ['paid', 'open', 'shipped']",
        "COUNT orders VALUE ==1400 WHERE status = 'paid'",
        "SUM orders.amount VALUE ==209000 TOLERANCE 1%",
        "FRESH orders.shipped_at WITHIN 2d",
        ""
      ].join("\n")
    },
    {
      name: "Namespaces and composite keys",
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

  // The Python side lives in glue.py next to this file (docs.yml smoke-tests it).

  var SQL_KEYWORDS = /\b(SELECT|FROM|WHERE|GROUP BY|ORDER BY|HAVING|LEFT JOIN|INNER JOIN|CROSS JOIN|JOIN|ON|AS|AND|OR|NOT|IN|IS|NULL|CASE|WHEN|THEN|ELSE|END|UNION ALL|LIMIT|TOP|FETCH FIRST|ROWS ONLY|CAST|COALESCE|DISTINCT|COUNT|COUNT_BIG|COUNTIF|COUNT_IF|SUM|AVG|MIN|MAX|FILTER|LOWER|TRIM|LIKE|WITH)\b/g;

  function esc(text) {
    return String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function highlightSql(sql) {
    return esc(sql)
      .replace(/'(?:[^']|'')*'/g, function (s) { return '<span class="fl-tok-str">' + s + "</span>"; })
      .replace(SQL_KEYWORDS, '<span class="fl-tok-kw">$1</span>');
  }

  function highlightClaims(text) {
    return window.FactLangHighlight ? window.FactLangHighlight(text) : esc(text);
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

  var runtime = null; // a Promise of the ready Pyodide instance, shared across page visits

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

  function renderLint(data) {
    if (!data.diagnostics.length) {
      return '<p class="fl-pg__ok"><span class="fl-verdict fl-verdict--pass">clean</span> ' +
        data.claims + (data.claims === 1 ? " claim" : " claims") + ", no diagnostics.</p>";
    }
    var counts = {};
    data.diagnostics.forEach(function (d) { counts[d.severity] = (counts[d.severity] || 0) + 1; });
    var summary = Object.keys(counts).map(function (k) { return counts[k] + " " + k + (counts[k] > 1 ? "s" : ""); }).join(", ");
    var rendered = esc(data.rendered)
      .replace(/^(error)(\[FL\d{3}\])/gm, '<span class="fl-pg__sev fl-pg__sev--error">$1$2</span>')
      .replace(/^(warning)(\[FL\d{3}\])/gm, '<span class="fl-pg__sev fl-pg__sev--warning">$1$2</span>')
      .replace(/^(info)(\[FL\d{3}\])/gm, '<span class="fl-pg__sev fl-pg__sev--info">$1$2</span>')
      .replace(/^(\s*\^+)$/gm, '<span class="fl-pg__caret">$1</span>')
      .replace(/^(did you mean: .*|help: .*)$/gm, '<span class="fl-pg__help">$1</span>');
    var docs = data.diagnostics.map(function (d) { return d.code; })
      .filter(function (c, i, a) { return a.indexOf(c) === i; })
      .map(function (c) { return '<a href="../reference/diagnostics/#' + c.toLowerCase() + '">' + c + "</a>"; }).join(" ");
    return '<p class="fl-pg__summary">' + esc(summary) + " · codes: " + docs + "</p>" +
      '<pre class="fl-pg__diag">' + rendered + "</pre>";
  }

  function renderFormat(data) {
    if (data.error) {
      return '<p class="fl-pg__summary">The text has syntax errors, so it cannot be formatted without losing lines. Lint it to see them.</p>' +
        '<pre class="fl-pg__diag">' + esc(data.error) + "</pre>";
    }
    return '<p class="fl-pg__summary">Canonical form. <button type="button" class="fl-pg__link" data-apply>Replace the editor text</button></p>' +
      '<pre class="fl-pg__code"><code>' + highlightClaims(data.text) + "</code></pre>";
  }

  function renderExplain(data) {
    if (!data.claims.length) return '<p class="fl-pg__summary">No claims to explain.</p>';
    return data.claims.map(function (c) {
      var head = '<div class="fl-pg__claim"><code>' + highlightClaims(c.claim) + "</code></div>";
      if (c.kind === "outcome") {
        return '<section class="fl-pg__probe">' + head + '<p><span class="fl-verdict fl-verdict--' + esc(c.verdict) + '">' +
          esc(c.verdict.replace("_", " ")) + "</span> " + esc(c.explanation) + "</p></section>";
      }
      var params = c.params.length
        ? '<details><summary>Executed form: template and ' + c.params.length + " bind parameter" + (c.params.length > 1 ? "s" : "") + "</summary>" +
          '<pre class="fl-pg__code"><code>' + highlightSql(c.template) + "</code></pre><table><tr><th>name</th><th>value</th><th>type family</th></tr>" +
          c.params.map(function (p) { return "<tr><td>" + esc(p[0]) + "</td><td><code>" + esc(p[1]) + "</code></td><td>" + esc(p[2]) + "</td></tr>"; }).join("") +
          "</table></details>"
        : "";
      return '<section class="fl-pg__probe">' + head +
        '<p class="fl-pg__meta">' + esc(data.dialect) + ', tier <a href="../guide/evidence/#tiers">' + esc(c.tier) + "</a></p>" +
        '<pre class="fl-pg__code"><code>' + highlightSql(c.sql) + "</code></pre>" + params + "</section>";
    }).join("");
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
    var lastAction = "explain";
    var lintTimer = null;
    var py = null;

    function status(text, isError) {
      statusEl.textContent = text || "";
      statusEl.hidden = !text;
      statusEl.classList.toggle("fl-pg__status--error", !!isError);
    }

    function paint() {
      hl.innerHTML = highlightClaims(source.value) + "\n";
      hlPre.scrollTop = source.scrollTop;
      hlPre.scrollLeft = source.scrollLeft;
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

    var shared = /^#claims=/.test(location.hash) ? decodeShare(location.hash.slice(8)) : null;
    source.value = shared !== null ? shared : EXAMPLES[0].text;
    paint();

    function run(action) {
      if (!py) return;
      lastAction = action;
      var text = source.value;
      try {
        var data;
        if (action === "lint") {
          data = JSON.parse(py.globals.get("fl_lint")(text));
          result.innerHTML = renderLint(data);
        } else if (action === "format") {
          data = JSON.parse(py.globals.get("fl_format")(text));
          result.innerHTML = renderFormat(data);
          var apply = result.querySelector("[data-apply]");
          if (apply) apply.addEventListener("click", function () { source.value = data.text; paint(); });
        } else {
          data = JSON.parse(py.globals.get("fl_explain")(text, dialect.value));
          result.innerHTML = renderExplain(data);
        }
        buttons.forEach(function (b) { b.setAttribute("aria-pressed", b.dataset.action === action ? "true" : "false"); });
      } catch (err) {
        result.innerHTML = '<p class="fl-pg__summary fl-pg__status--error">' + esc(err.message || err) + "</p>";
      }
    }

    source.addEventListener("input", function () {
      paint();
      if (!py) return;
      window.clearTimeout(lintTimer);
      lintTimer = window.setTimeout(function () { run(lastAction === "explain" ? "explain" : "lint"); }, 350);
    });
    source.addEventListener("scroll", paint);
    source.addEventListener("keydown", function (ev) {
      if ((ev.ctrlKey || ev.metaKey) && ev.key === "Enter") {
        ev.preventDefault();
        run(lastAction);
      }
    });
    buttons.forEach(function (b) {
      b.addEventListener("click", function () { run(b.dataset.action); });
    });
    dialect.addEventListener("change", function () {
      try { window.localStorage.setItem("factlang.playground.dialect", dialect.value); } catch (e) { /* ignore */ }
      if (lastAction === "explain") run("explain");
    });
    example.addEventListener("change", function () {
      source.value = EXAMPLES[Number(example.value)].text;
      paint();
      run(Number(example.value) === EXAMPLES.length - 1 ? "lint" : lastAction);
    });
    root.querySelector("#fl-pg-share").addEventListener("click", function (ev) {
      var url = location.href.split("#")[0] + "#claims=" + encodeShare(source.value);
      history.replaceState(null, "", url);
      var button = ev.currentTarget;
      var done = function () { button.textContent = "Link copied"; window.setTimeout(function () { button.textContent = "Copy a link to these claims"; }, 1800); };
      if (navigator.clipboard) navigator.clipboard.writeText(url).then(done, done); else done();
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

    startPython(root, status).then(function (pyodide) {
      py = pyodide;
      try { fillDialects(JSON.parse(py.globals.get("FL_DIALECTS"))); } catch (e) { /* keep the static list */ }
      status("");
      root.classList.add("fl-pg--ready");
      buttons.forEach(function (b) { b.disabled = false; });
      run("explain");
    }).catch(function (err) {
      status("The playground could not start: " + (err.message || err) + ". Reload to try again, or run FactLang locally with pip install factlang.", true);
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
