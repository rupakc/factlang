"""The documentation site is executable (spec 14.3).

- Every ```python block of the pages in ``EXECUTED`` runs, in order, in one namespace per
  page, against an in-memory DuckDB (the brief requires getting-started and extending/verbs;
  the other pages are held to the same standard).
- Every ```factlang block anywhere on the site lints without errors.
- ``README.md`` is held to the same standard: its python blocks run, in order, and every
  claim in its verb table lints cleanly.
- The landing page's ledger shows real outcomes of the getting-started shop.
- ``docs/reference/verbs.md`` is what ``scripts/gen_reference.py`` generates.
"""

from __future__ import annotations

import html
import importlib.util
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

import factlang as fl
from factlang.ir import PRIMITIVE_LOWERINGS
from factlang.semantics import types as types_mod
from factlang.sql import registry as sql_registry

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"

REQUIRED = ("getting-started.md", "extending/verbs.md")
EXECUTED = (
    *REQUIRED,
    "index.md",
    "agents.md",
    "guide/language.md",
    "guide/evidence.md",
    "guide/measure.md",
    "extending/packs.md",
    "extending/types.md",
    "extending/dialects.md",
    "extending/connectors.md",
    "extending/catalogs.md",
    "extending/primitives.md",
    "extending/fragments.md",
)
# Pages that are not part of the built site (see exclude_docs in mkdocs.yml), and the
# grammar, whose blocks tests/conformance/test_grammar_doc.py checks with their own codes.
NOT_LINTED = ("specs/", "wiki/", "overrides/", "reference/grammar.md")

_PYTHON = re.compile(r"^```python[ \t]*\n(.*?)^```", re.M | re.S)
_FACTLANG = re.compile(r"^[ \t]*```factlang[ \t]*\n(.*?)^[ \t]*```", re.M | re.S)


def _site_pages() -> list[Path]:
    return [
        p
        for p in sorted(DOCS.rglob("*.md"))
        if not any(p.relative_to(DOCS).as_posix().startswith(x) for x in NOT_LINTED)
    ]


@pytest.fixture(autouse=True)
def _restore_global_registries() -> Iterator[None]:
    """Pages register dialects, value types and primitives; none may leak into other tests."""
    profiles = dict(sql_registry._PROFILES)
    value_types = dict(types_mod._REGISTRY)
    lowerings = dict(PRIMITIVE_LOWERINGS)
    yield
    sql_registry._PROFILES.clear()
    sql_registry._PROFILES.update(profiles)
    types_mod._REGISTRY.clear()
    types_mod._REGISTRY.update(value_types)
    PRIMITIVE_LOWERINGS.clear()
    PRIMITIVE_LOWERINGS.update(lowerings)


def _run_page(
    page: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, root: Path = DOCS
) -> dict[str, Any]:
    blocks = _PYTHON.findall((root / page).read_text(encoding="utf-8"))
    assert blocks, f"{page} has no python blocks"
    monkeypatch.chdir(tmp_path)
    ns: dict[str, Any] = {"__name__": f"docs:{page}"}
    for i, block in enumerate(blocks, start=1):
        exec(compile(block, f"<{page} block {i}>", "exec", dont_inherit=True), ns)
    return ns


def test_required_pages_are_executed() -> None:
    assert set(REQUIRED) <= set(EXECUTED)


@pytest.mark.parametrize("page", EXECUTED)
def test_python_blocks_run(page: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("duckdb")
    _run_page(page, tmp_path, monkeypatch)


def test_readme_python_blocks_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("duckdb")
    ns = _run_page("README.md", tmp_path, monkeypatch, root=ROOT)
    assert ns["report"].outcomes  # the thirty-second example really checked something


_VERB_ROW = re.compile(r"^\| `([A-Z]+)` \| `(.*?)` \|", re.M)


def test_readme_verb_table_is_the_standard_pack_and_lints() -> None:
    rows = _VERB_ROW.findall((ROOT / "README.md").read_text(encoding="utf-8"))
    assert sorted(verb for verb, _ in rows) == sorted(fl.standard_pack().names())
    for verb, claim in rows:
        assert claim.split()[0] == verb, claim
        bag = fl.lint(claim)
        assert not bag.has_errors, f"{claim}\n{bag.render()}"


@pytest.mark.parametrize("page", _site_pages(), ids=lambda p: p.relative_to(DOCS).as_posix())
def test_factlang_blocks_lint_cleanly(page: Path) -> None:
    for block in _FACTLANG.findall(page.read_text(encoding="utf-8")):
        bag = fl.lint(block)
        assert not bag.has_errors, f"{page.name}:\n{block}\n{bag.render()}"


def test_landing_ledger_is_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Every row of the hero ledger is the real outcome on the getting-started shop."""
    pytest.importorskip("duckdb")
    page = (DOCS / "getting-started.md").read_text(encoding="utf-8")
    ns: dict[str, Any] = {}
    monkeypatch.chdir(tmp_path)
    exec(
        compile(_PYTHON.findall(page)[0], "<getting-started block 1>", "exec", dont_inherit=True),
        ns,
    )
    home = (DOCS / "overrides" / "home.html").read_text(encoding="utf-8")
    rows = re.findall(
        r'<div class="fl-ledger__row" data-verdict="(\w+)">\s*'
        r'<code class="fl-ledger__claim[^"]*">(.*?)</code>.*?'
        r'<span class="fl-ledger__why">(.*?)</span>',
        home,
        re.S,
    )
    assert len(rows) >= 5
    checker = fl.Checker(ns["connector"])
    for verdict, claim, why in rows:
        (outcome,) = checker.check(html.unescape(claim)).outcomes
        assert outcome.verdict.value == verdict, claim
        assert outcome.explanation == html.unescape(why), claim


def test_playground_glue_smoke() -> None:
    """The Python the playground runs in Pyodide works against this source tree."""
    path = ROOT / "site-playground" / "glue.py"
    ns: dict[str, Any] = {"__name__": "glue"}
    exec(compile(path.read_text(encoding="utf-8"), str(path), "exec", dont_inherit=True), ns)
    assert ns["smoke"]().startswith("ok ")
    lint = json.loads(ns["fl_lint"]("UNIQE orders.id\nNULLS t.c RATE <=2\n"))
    lines = [d["span"]["line"] for d in lint["diagnostics"]]
    assert lines == sorted(lines) and len(lines) == 2


def _gen_reference() -> Any:
    spec = importlib.util.spec_from_file_location(
        "gen_reference", ROOT / "scripts" / "gen_reference.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_verb_reference_is_generated_and_current() -> None:
    text = (DOCS / "reference" / "verbs.md").read_text(encoding="utf-8")
    assert text == _gen_reference().render(), "run: python scripts/gen_reference.py"
    for name in fl.standard_pack().names():
        assert f"### {name}\n" in text, name
