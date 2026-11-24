---
name: release
description: Cut a FactLang release. Use when asked to publish a new version.
---

# Release

1. Confirm `main` is green: `gh run list --branch main --limit 1`.
2. Bump `__version__` in `src/factlang/__about__.py` (SemVer). Change `LANGUAGE_VERSION` only if the language itself changed.
3. Move the `[Unreleased]` changelog entries under the new version with the date.
4. Run the full gate plus `python -m build` and `twine check dist/*`; install the wheel in a clean venv and run `factlang --version`.
5. Commit `chore: release vX.Y.Z`, push, wait for CI green.
6. Tag `vX.Y.Z` and push the tag; `release.yml` builds, creates the GitHub Release and publishes through Trusted Publishing (environment `pypi`).
7. Verify the release page, the PyPI page and `pip install factlang==X.Y.Z` in a clean venv.
8. Never rewrite a published tag; fix forward with a patch release.
