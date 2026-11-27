"""Shared pieces of the browser e2e tests (``conftest.py`` and ``test_site_e2e.py``).

A module of its own, with a unique name, so the test module never imports another
directory's ``conftest``.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
SITE_URL = os.environ.get("FL_SITE_URL", "").strip()
CDN_HOSTS = ("cdn.jsdelivr.net", "extensions.duckdb.org")
CDN_ATTEMPTS = 5
# a playground page boots Pyodide and DuckDB-WASM from the CDN: generous, but bounded
BOOT_MS = 300_000

# Counts Workers (DuckDB's starts from a blob: URL) and Pyodide boots, and keeps the Pyodide
# instance so a test can reach the engine through engine.js's own idempotent startEngine.
INIT_SCRIPT = """
(() => {
  const fl = window.__fl = { workers: 0, blobWorkers: 0, pyodideLoads: 0, pyodide: null };
  const W = window.Worker;
  window.Worker = function (url, options) {
    fl.workers++;
    if (String(url).startsWith("blob:")) fl.blobWorkers++;
    return new W(url, options);
  };
  window.Worker.prototype = W.prototype;
  // A window accessor for loadPyodide does not see pyodide.js define it (measured), so wrap
  // it at the moment the playground's own onload handler for that script runs.
  function wrapLoad() {
    const real = window.loadPyodide;
    if (typeof real !== "function" || real.__flWrapped) return;
    const wrapped = async function (options) {
      fl.pyodideLoads++;
      const pyodide = await real(options);
      fl.pyodide = pyodide;
      return pyodide;
    };
    wrapped.__flWrapped = true;
    window.loadPyodide = wrapped;
  }
  const onload = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "onload");
  Object.defineProperty(HTMLScriptElement.prototype, "onload", {
    configurable: true,
    get() { return onload.get.call(this); },
    set(handler) {
      const el = this;
      onload.set.call(this, typeof handler === "function"
        ? function (event) { wrapLoad(); return handler.call(el, event); }
        : handler);
    }
  });
})();
"""


def production_site_url() -> str:
    found = re.search(
        r"^site_url:\s*(\S+)", (ROOT / "mkdocs.yml").read_text(encoding="utf-8"), re.M
    )
    return found.group(1) if found else ""


@dataclass
class Seen:
    """Everything one browser context requested."""

    requests: list[Any] = field(default_factory=list)
    responses: list[Any] = field(default_factory=list)
    dialogs: list[str] = field(default_factory=list)
    page_errors: list[str] = field(default_factory=list)
    console: list[str] = field(default_factory=list)

    def urls(self) -> list[str]:
        return [r.url for r in self.requests]

    def hosts(self) -> set[str]:
        """Network hosts contacted; ``blob:`` / ``data:`` URLs are local and not listed."""
        out = set()
        for url in self.urls():
            parts = urlsplit(url)
            if parts.scheme in ("http", "https", "ws", "wss"):
                out.add(parts.hostname or "")
        return out


class CdnCache:
    """Fetch CDN responses with retries, once per session."""

    def __init__(self) -> None:
        self.store: dict[str, tuple[int, dict[str, str], bytes]] = {}
        self.failures: list[str] = []

    def handle(self, route: Any) -> None:
        request = route.request
        if request.method != "GET":
            route.continue_()
            return
        hit = self.store.get(request.url)
        if hit is None:
            hit = self._fetch(route)
            if hit is None:
                route.abort()
                return
        status, headers, body = hit
        route.fulfill(status=status, headers=headers, body=body)

    def _fetch(self, route: Any) -> tuple[int, dict[str, str], bytes] | None:
        last = ""
        for attempt in range(CDN_ATTEMPTS):
            if attempt:
                time.sleep(min(2**attempt, 20))
            try:
                response = route.fetch(timeout=90_000)
            except Exception as exc:  # network error: retry
                last = str(exc)
                continue
            if response.status >= 500:
                last = f"HTTP {response.status}"
                continue
            body = response.body()
            # the body is already decoded: drop the transfer headers that describe the wire form
            headers = {
                k: v
                for k, v in response.headers.items()
                if k.lower() not in ("content-encoding", "content-length", "transfer-encoding")
            }
            hit = (response.status, headers, body)
            if response.status == 200:
                self.store[route.request.url] = hit
            return hit
        self.failures.append(f"{route.request.url}: {last}")
        return None
