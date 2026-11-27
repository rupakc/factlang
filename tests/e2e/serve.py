"""Serve a built site for the e2e tests: ``python tests/e2e/serve.py <dir> [port] [base]``.

``python -m http.server`` keeps the standard library's listen backlog of 5. A page load
opens more connections than that at once (Material's assets, the playground's scripts), and
on macOS the overflow is reset (measured: ``net::ERR_CONNECTION_RESET`` on
``assets/playground/playground.js``, so the playground never started). This is the same
threaded server with a deeper backlog, bound to 127.0.0.1 only.

``base`` (e.g. ``/factlang/``) serves the site under that path, as GitHub Pages does for a
project site; a missing page inside it gets the site's ``404.html``, every URL outside it a
plain 404 HTML page, as there. Serving from ``/`` hides
URL bugs that resolve one level too high: they land on the site root and still work
(2026-11-27: the playground reached by instant navigation from a guide page fetched
``/assets/playground/wheel.json``, a 404 under ``/factlang/`` only).
"""

from __future__ import annotations

import functools
import os
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class Server(ThreadingHTTPServer):
    request_queue_size = 256
    daemon_threads = True


class Handler(SimpleHTTPRequestHandler):
    base = "/"

    def translate_path(self, path: str) -> str:
        url_path = urlsplit(path).path
        if not url_path.startswith(self.base):
            # a path that cannot exist: SimpleHTTPRequestHandler answers 404 (HTML)
            return os.path.join(self.directory, "__outside_base__", "missing")
        return super().translate_path("/" + path[len(self.base) :])

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        """A missing page inside the site gets the site's own ``404.html`` (status 404), as
        GitHub Pages answers; outside the site, the plain error page."""
        page = os.path.join(self.directory, "404.html")
        inside = urlsplit(self.path).path.startswith(self.base)
        if code != 404 or not inside or not os.path.isfile(page):
            super().send_error(code, message, explain)
            return
        with open(page, "rb") as handle:
            body = handle.read()
        self.send_response(404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)


def normalise_base(base: str) -> str:
    return "/" + base.strip("/") + "/" if base.strip("/") else "/"


def main(argv: list[str]) -> None:
    directory = argv[1] if len(argv) > 1 else "site"
    port = int(argv[2]) if len(argv) > 2 else 8000
    base = normalise_base(argv[3] if len(argv) > 3 else "/")
    handler = functools.partial(
        type("BasedHandler", (Handler,), {"base": base}), directory=directory
    )
    with Server(("127.0.0.1", port), handler) as httpd:
        print(f"serving {directory} on http://127.0.0.1:{port}{base}", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main(sys.argv)
