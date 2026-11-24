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
