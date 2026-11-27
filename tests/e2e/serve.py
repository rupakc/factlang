"""Serve a built site for the e2e tests: ``python tests/e2e/serve.py <dir> [port]``.

``python -m http.server`` keeps the standard library's listen backlog of 5. A page load
opens more connections than that at once (Material's assets, the playground's scripts), and
on macOS the overflow is reset (measured: ``net::ERR_CONNECTION_RESET`` on
``assets/playground/playground.js``, so the playground never started). This is the same
threaded server with a deeper backlog, bound to 127.0.0.1 only.
"""

from __future__ import annotations

import functools
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer


class Server(ThreadingHTTPServer):
    request_queue_size = 256
    daemon_threads = True


def main(argv: list[str]) -> None:
    directory = argv[1] if len(argv) > 1 else "site"
    port = int(argv[2]) if len(argv) > 2 else 8000
    handler = functools.partial(SimpleHTTPRequestHandler, directory=directory)
    with Server(("127.0.0.1", port), handler) as httpd:
        print(f"serving {directory} on http://127.0.0.1:{port}/", flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main(sys.argv)
