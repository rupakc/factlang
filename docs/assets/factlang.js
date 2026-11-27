/* FactLang site script: syntax highlighting for FactLang code blocks, the landing-page
 * ledger animation, fade-up on scroll (.fl-fade-up -> .is-visible) and copy buttons
 * (.fl-copy). No dependencies; safe to run more than once (Material instant nav). */
(function () {
  "use strict";

  /* Lets the stylesheet hide .fl-fade-up content only when this script runs. */
  document.documentElement.classList.add("fl-js");

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

  function prefersReducedMotion() {
    return !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
  }

  /* Fade-up: one observer for the whole session; each run observes only elements not yet
   * seen, so instant navigation never double-registers. Without IntersectionObserver or
   * with reduced motion, everything is shown at once. */
  var fadeObserver = null;

  function revealOnScroll(root) {
    var items = root.querySelectorAll(".fl-fade-up:not(.is-visible)");
    if (!items.length) return;
    if (prefersReducedMotion() || !("IntersectionObserver" in window)) {
      Array.prototype.forEach.call(items, function (el) { el.classList.add("is-visible"); });
      return;
    }
    if (!fadeObserver) {
      fadeObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          fadeObserver.unobserve(entry.target);
        });
      }, { rootMargin: "0px 0px -8% 0px", threshold: 0.08 });
    }
    Array.prototype.forEach.call(items, function (el) {
      if (el.dataset.flFadeObserved) return;
      el.dataset.flFadeObserved = "1";
      fadeObserver.observe(el);
    });
  }

  /* Copy buttons. The text is data-copy, else the text of the element matched by
   * data-copy-target, else the first <code> in the button's parent. One delegated listener
   * for the whole session. */
  function copyText(button) {
    if (button.hasAttribute("data-copy")) return button.getAttribute("data-copy");
    var target = button.getAttribute("data-copy-target");
    var el = target ? document.querySelector(target) : button.parentNode && button.parentNode.querySelector("code");
    return el ? el.textContent.trim() : "";
  }

  function writeClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
    return new Promise(function (resolve, reject) {
      var area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(area);
      if (ok) resolve(); else reject(new Error("copy failed"));
    });
  }

  function onCopyClick(event) {
    var button = event.target.closest && event.target.closest(".fl-copy");
    if (!button) return;
    event.preventDefault();
    var text = copyText(button);
    if (!text) return;
    var label = button.querySelector(".fl-copy__label");
    if (!button.dataset.flLabel) button.dataset.flLabel = label ? label.textContent : button.textContent;
    if (button.dataset.flAria === undefined) button.dataset.flAria = button.getAttribute("aria-label") || "";
    writeClipboard(text).then(function () {
      button.classList.add("is-copied");
      if (label) label.textContent = "Copied"; else if (!button.children.length) button.textContent = "Copied";
      button.setAttribute("aria-label", "Copied to clipboard");
      window.clearTimeout(button._flCopyTimer);
      button._flCopyTimer = window.setTimeout(function () {
        button.classList.remove("is-copied");
        if (label) label.textContent = button.dataset.flLabel;
        else if (!button.children.length) button.textContent = button.dataset.flLabel;
        if (button.dataset.flAria) button.setAttribute("aria-label", button.dataset.flAria);
        else button.removeAttribute("aria-label");
      }, 1600);
    }, function () {
      button.setAttribute("title", "Copy failed: select the text and copy it by hand");
    });
  }

  if (!window.__flCopyBound) {
    window.__flCopyBound = true;
    document.addEventListener("click", onCopyClick);
  }

  function run() {
    highlightBlocks(document);
    animateLedger(document);
    revealOnScroll(document);
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
