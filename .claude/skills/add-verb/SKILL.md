---
name: add-verb
description: Add a verb to the FactLang standard pack or a user pack. Use when asked to add a new claim verb.
---

# Add a verb

1. Read spec section 6 (verb pack) and 4 (language) for naming and operand rules.
2. Create exactly one file for the verb: `src/factlang/packs/standard/<verb>.py` for the standard pack, or a module in the user pack.
3. Declare the `VerbSpec`: operands, attributes, and a `Contract` with `plan` (probe IR) and `judge` (predicate); use only metric aliases from the wire format.
4. Provide at least 3 valid and at least 3 invalid examples on the contract (each invalid one names its expected FLnnn code); use the generic shop domain only.
5. Register the verb in the pack (standard pack `__init__`, or the `factlang.packs` entry point for a user pack).
6. Imports: stdlib and strictly lower layers only (`tests/test_architecture.py`).
7. Run `pytest tests/unit/packs tests/conformance`, then `pytest`, `ruff check`, `ruff format --check`, `mypy --strict src`.
8. Add the verb to `docs/reference/verbs.md` and `CHANGELOG.md`.
