/* FactLang site script: syntax highlighting for FactLang code blocks and the landing-page
 * ledger animation. No dependencies; safe to run more than once (Material instant nav). */
(function () {
  "use strict";

  var FILTER_KEYWORDS = {
    WHERE: 1, AND: 1, OR: 1, NOT: 1, IN: 1, IS: 1, NULL: 1, BETWEEN: 1,
    TRUE: 1, FALSE: 1, DATE: 1, TIMESTAMP: 1, USE: 1
  };

  var TOKEN = new RegExp(
    [
      "(#.*$)", // 1 comment
      "('(?:[^'\\n]|'')*'?)", // 2 string
      "(\"(?:[^\"\\n]|\"\")*\"?|`(?:[^`\\n]|``)*`?)", // 3 quoted identifier
      "(\\b(?:1:1|1:N|N:1|N:M)\\b)", // 4 cardinality
      "(-?\\d[\\d_]*(?:\\.\\d+)?(?:[eE][-+]?\\d+)?%?(?:[smhdw](?![A-Za-z0-9_]))?)", // 5 number
      "(->|>=|<=|==|!=|=|<|>)", // 6 operator
      "([A-Za-z_][A-Za-z0-9_$]*)" // 7 word
    ].join("|"),
    "gm"
  );

  function escapeHtml(text) {
    return text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function isUpperWord(word) {
    return /[A-Z]/.test(word) && !/[a-z]/.test(word);
  }

  /* Highlight FactLang source. The first uppercase word of a claim is the verb; later
   * uppercase words are attributes, except filter keywords after WHERE. */
  function highlight(source) {
    var out = "";
    source.split("\n").forEach(function (line, index, lines) {
      var last = 0;
      var sawVerb = /^\s/.test(line) && index > 0 && /\\\s*$/.test(lines[index - 1]);
      var inFilter = false;
      var match;
      TOKEN.lastIndex = 0;
      while ((match = TOKEN.exec(line)) !== null) {
        if (match[0] === "") { TOKEN.lastIndex++; continue; }
        out += escapeHtml(line.slice(last, match.index));
        var text = escapeHtml(match[0]);
        var cls = null;
        if (match[1]) cls = "com";
        else if (match[2]) cls = "str";
        else if (match[3]) cls = "qid";
        else if (match[4] || match[5]) cls = "num";
        else if (match[6]) cls = "op";
        else if (match[7]) {
          var word = match[7];
          if (word === "WHERE") { cls = "kw"; inFilter = true; }
          else if (word === "USE" && !sawVerb) { cls = "kw"; sawVerb = true; }
          else if (inFilter && FILTER_KEYWORDS[word]) cls = "kw";
          else if (!inFilter && isUpperWord(word)) {
            if (!sawVerb) { cls = "verb"; sawVerb = true; }
            else if ((word === "DATE" || word === "TIMESTAMP")) cls = "kw";
            else cls = "attr";
          } else {
            sawVerb = true;
          }
        }
        out += cls ? '<span class="fl-tok-' + cls + '">' + text + "</span>" : text;
        last = match.index + match[0].length;
      }
      out += escapeHtml(line.slice(last));
      if (index < lines.length - 1) out += "\n";
    });
    return out;
  }

  function highlightBlocks(root) {
    var blocks = root.querySelectorAll(
      '[class*="language-factlang"] code, code.language-factlang, .fl-src'
    );
    Array.prototype.forEach.call(blocks, function (code) {
      if (code.dataset.flHighlighted) return;
      code.innerHTML = highlight(code.textContent);
      code.dataset.flHighlighted = "1";
    });
  }

  function animateLedger(root) {
    var ledger = root.querySelector(".fl-ledger");
    if (!ledger || ledger.dataset.flAnimated) return;
    ledger.dataset.flAnimated = "1";
    var reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reduce) return;
    var rows = ledger.querySelectorAll(".fl-ledger__row");
    ledger.classList.add("fl-anim");
    Array.prototype.forEach.call(rows, function (row, i) {
      window.setTimeout(function () { row.classList.add("is-written"); }, 250 + i * 520);
      window.setTimeout(function () { row.classList.add("is-judged"); }, 250 + rows.length * 520 + 300 + i * 260);
    });
  }

  function run() {
    highlightBlocks(document);
    animateLedger(document);
  }

  window.FactLangHighlight = highlight;

  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(run);
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", run);
  } else {
    run();
  }
})();
