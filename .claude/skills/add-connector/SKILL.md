---
name: add-connector
description: Add a connector (warehouse adapter) to FactLang. Use when asked to execute claims on a new engine.
---

# Add a connector

1. Read spec sections 8.4 and 9 (connectors, catalogs, execution).
2. Create `src/factlang/connectors/<adapter>.py` implementing the `Connector` protocol from `connectors/base.py`; declare its dialect name and its paramstyle (it calls `probe.bind(paramstyle)`).
3. Import the vendor driver lazily inside functions only; the module itself must import cleanly without the driver installed.
4. Add the optional dependency as an extra in `pyproject.toml` (and to `all`).
5. Connections must be read-only where the driver allows; never execute anything but the bound probe SQL; never log parameter values at INFO.
6. Add unit tests with a fake driver, and an `integration`-marked test against a real engine or service container.
7. Add a CI service container if the engine has an image.
8. Run `pytest`, `ruff check`, `ruff format --check`, `mypy --strict src`; update docs and `CHANGELOG.md`.
