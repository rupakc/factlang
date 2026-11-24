---
name: run-conformance
description: Run the FactLang conformance suites and interpret failures. Use before merging dialect, IR or verb changes.
---

# Run conformance

1. Activate the venv: `. .venv/bin/activate` (create with `python3 -m venv .venv && pip install -e .[dev]`).
2. Unit and grammar conformance: `pytest tests/unit tests/conformance -q`.
3. Dialect conformance only: `pytest tests/conformance -q -k dialect`.
4. Engines available locally (duckdb is in the dev extra): `pytest -m integration -q`. Service-container engines run in CI.
5. A failure in the metric-alias test means the wire format changed: that is a breaking change, never update the expected aliases to make it pass.
6. A failure in only one dialect: fix that profile; do not weaken the shared assertion.
7. Finish with the full gate: `pytest`, `ruff check`, `ruff format --check`, `mypy --strict src`.
