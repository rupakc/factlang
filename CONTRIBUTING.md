# Contributing to FactLang

Thanks for helping. Please read `CLAUDE.md`: it holds the project rules for humans
and agents alike.

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

## Checks (all blocking in CI)

```bash
pytest
ruff check
ruff format --check
mypy --strict src
```

## The site and the playground

The site is MkDocs Material (`mkdocs.yml`, `docs/`); the playground's sources live in
`site-playground/` and are published by `scripts/docs_hooks.py` together with the wheel.
The built-site tests and the playground parity tests need their extras, and skip without
them:

```bash
pip install -e ".[dev,docs,playground]"   # duckdb==1.4.3, the engine DuckDB-WASM embeds
python -m build --wheel                   # the wheel the playground installs
pytest tests/playground tests/conformance
mkdocs build --strict
```

The browser end-to-end tests (`tests/e2e`) drive a served build of the site in Chromium
(and WebKit for one layout check) with the real CDNs. They are skipped unless
`FL_SITE_URL` is set:

```bash
pip install -e ".[e2e]"
python -m playwright install chromium webkit
mkdocs build --strict -d site
python tests/e2e/serve.py site 8000 &   # http.server with a deeper listen backlog
FL_SITE_URL=http://127.0.0.1:8000/ pytest -rs tests/e2e
```

The playground honours two test-only URL parameters on localhost only (`?fl-timeout=<s>`,
`?fl-slow=<n>`, documented in `site-playground/engine.js`). The WebKit check skips when
WebKit is not installed, unless `FL_E2E_WEBKIT=1`. CI runs the e2e suite in the `site` job of
`ci.yml`, and `docs.yml` runs it again on the exact build it is about to publish.

## Rules in short

- The core imports only the standard library; optional dependencies are imported
  lazily inside functions.
- A module imports only from strictly lower layers (`tests/test_architecture.py`).
- No input sampling, no raw SQL in claims, every literal is a typed bind parameter.
- Every invariant ships with a negative control: a test that fails when the
  invariant is broken.
- Conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`), and every
  commit leaves the test suite green.

## Pull requests

Keep them focused, describe the behaviour change, and add tests. Update
`CHANGELOG.md` for user-visible changes.
