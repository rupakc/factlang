# Contributing

> Contributions are welcome: bug reports, new verbs, dialect profiles, connectors, docs.
> The rules are few but strict: layers are enforced, the core is standard-library only, claims never sample, and every invariant ships with a test that fails when it breaks.
> The repository's `CONTRIBUTING.md` and `CLAUDE.md` are the short, authoritative version of this page.

## Set up

```
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
```

## Checks that must pass

```
pytest
ruff check
ruff format --check
mypy --strict src
```

CI runs a fast matrix on every push and pull request (Python 3.10 to 3.13 on Ubuntu, plus 3.13 on Windows) and a full matrix of 3.10 to 3.13 on Ubuntu, macOS and Windows nightly, on manual dispatch and on version tags. It also runs integration tests on real engines (PostgreSQL, MySQL, ClickHouse, SQL Server and Trino service containers, plus DuckDB in process), enforces a 90% coverage gate, and builds and checks the package.

## The rules

1. A module imports only from strictly lower layers ([[Architecture]]); `tests/test_architecture.py` enforces it.
2. The core imports only the standard library; optional libraries are imported lazily inside functions.
3. No sampling in claims, contracts or the analyzer; no raw SQL in claims; every literal is a typed bind parameter.
4. Metric aliases are a wire format; do not rename them.
5. Every verb is one file with at least three valid and three invalid examples.
6. Every invariant needs a negative control: a test that fails when the invariant is broken.
7. Examples use the generic shop domain: orders, customers, order_items, products, events, opportunities.

## Commits and pull requests

Conventional commits (`feat:`, `fix:`, `chore:`, `docs:`, `test:`), and every commit leaves the test suite green. Keep pull requests focused, describe the behaviour change, add tests, and update `CHANGELOG.md` for user-visible changes.

## Where to start

- Add a verb: [[Extending-Verbs]]
- Add a dialect: [[Extending-Dialects]]
- Add a connector: [[Extending-Connectors]]
- Improve this wiki: edit `docs/wiki/` in the main repository. A workflow syncs it to the GitHub wiki when the change reaches `main`, so do not edit pages in the web UI (they would be overwritten).

## Project decisions

Design choices are logged in `MEMORY.md` with the reason and how to apply them; the specification in `docs/specs/` is the single source of truth.

## See also

- [[Architecture]]
- [[Design-Decisions]]
- [[Extending-Verbs]]
- [[Home]]
