"""The playground page offers live verdicts (site spec 5.2): a data panel, CSV loading that
stays in the browser, a Check action, and the two sample-shop examples as its defaults.

Static checks on ``docs/playground.md`` and ``site-playground/playground.js``; the built page
is checked too when mkdocs is installed. The behaviour itself (verdicts, CSV loading, Reset,
instant navigation) is exercised in a real browser by the e2e suite.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "playground.md"
JS = ROOT / "site-playground" / "playground.js"
HOME = ROOT / "docs" / "overrides" / "home.html"
NODE = shutil.which("node")

ACTIONS = {"lint", "format", "explain", "check"}


def _assert_live_page(html: str) -> None:
    assert 'id="fl-playground"' in html
    assert re.search(r'<[^>]+id="fl-pg-data"', html), "the data panel"
    file_input = re.search(r'<input[^>]*type="file"[^>]*>', html)
    assert file_input, "a Load CSV control"
    assert 'accept=".csv"' in file_input.group(0)
    assert "multiple" in file_input.group(0)
    assert re.search(r'<button[^>]*data-action="check"[^>]*>\s*Check\s*</button>', html)
    assert "read in your browser and never uploaded" in html, "the privacy banner"
    assert re.search(r'id="fl-pg-reset"', html), "Reset"
    assert "Check always runs DuckDB" in html
    # the SQL-only playground's sentences are gone
    assert "names are not checked" not in html
    assert "stops at the SQL" not in html


def test_page_source_has_the_live_playground() -> None:
    _assert_live_page(PAGE.read_text(encoding="utf-8"))


def test_check_is_the_primary_action() -> None:
    page = PAGE.read_text(encoding="utf-8")
    primary = re.findall(r'<button[^>]*class="[^"]*fl-pg__btn--primary[^"]*"[^>]*>', page)
    assert len(primary) == 1 and 'data-action="check"' in primary[0]


def _js_sample_examples() -> list[dict[str, str]]:
    js = JS.read_text(encoding="utf-8")
    found = re.search(r"var SAMPLE_EXAMPLES = (\[.*?\]);\s*// END SAMPLE_EXAMPLES", js, re.S)
    assert found, "playground.js holds the sample examples as a JSON literal"
    data: list[dict[str, str]] = json.loads(found.group(1))
    return data


def test_default_examples_are_the_glue_sample_examples(glue: ModuleType) -> None:
    """The page shows the examples before Python has started, so it holds a copy: it must
    be glue.py's, byte for byte (glue's are the ones the tests prove)."""
    assert _js_sample_examples() == json.loads(glue.SAMPLE_EXAMPLES_JSON)


def test_every_example_declares_its_default_action() -> None:
    js = JS.read_text(encoding="utf-8")
    actions = re.findall(r'action:\s*"(\w+)"', js)
    assert actions, "the other examples declare an action"
    assert set(actions) <= ACTIONS
    assert all(ex["action"] in ACTIONS for ex in _js_sample_examples())
    # the old index-based rule ("the last example lints") is gone
    assert "EXAMPLES.length - 1" not in js


def test_share_links_carry_claims_only() -> None:
    js = JS.read_text(encoding="utf-8")
    shares = re.findall(r'"#claims=" \+ encodeShare\(([^)]*)\)', js)
    assert shares == ["source.value"]


def test_the_landing_page_describes_the_live_playground() -> None:
    home = HOME.read_text(encoding="utf-8")
    assert "see their diagnostics and SQL" not in home, "the SQL-only playground's card copy"
    card = re.search(
        r'<span class="fl-link__title">Playground</span>\s*<span[^>]*>(.*?)</span>', home, re.S
    )
    assert card is not None
    assert "verdict" in card.group(1) and "CSV" in card.group(1)
    cta = re.search(r'id="fl-cta-title">.*?</h2>\s*<p>(.*?)</p>', home, re.S)
    assert cta is not None
    assert "verdict" in cta.group(1) and "CSV" in cta.group(1)


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_script_is_inert_without_the_playground(tmp_path: Path) -> None:
    """playground.js is on every page (extra_javascript): without #fl-playground it must not
    download anything or touch the page."""
    script = tmp_path / "run.cjs"
    script.write_text(
        textwrap.dedent(
            """
            const vm = require("vm");
            const fs = require("fs");
            const touched = [];
            const document = {
              readyState: "complete",
              getElementById: (id) => { touched.push("get " + id); return null; },
              querySelector: () => { touched.push("query"); return null; },
              createElement: (t) => { touched.push("create " + t); return {}; },
              head: { appendChild: () => touched.push("append") },
              addEventListener: () => {},
            };
            const fetch = () => { touched.push("fetch"); };
            const window = { document, fetch, location: { hash: "" } };
            window.document$ = { subscribe: (fn) => fn() };
            const ctx = vm.createContext({ window, document, fetch, location: window.location });
            vm.runInContext(fs.readFileSync(process.argv[2], "utf8"), ctx);
            console.log(JSON.stringify(touched));
            """
        ),
        encoding="utf-8",
    )
    assert NODE is not None
    proc = subprocess.run([NODE, str(script), str(JS)], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout.strip().splitlines()[-1]) == ["get fl-playground"]


# -- the built page ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built_playground(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    if importlib.util.find_spec("mkdocs") is None or importlib.util.find_spec("material") is None:
        pytest.skip('mkdocs is not installed: pip install -e ".[docs]" to build the site')
    if not (ROOT / "mkdocs.yml").exists():
        pytest.skip("not a source checkout")
    out = tmp_path_factory.mktemp("site")
    env = {**os.environ, "NO_MKDOCS_2_WARNING": "1"}
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "mkdocs",
            "build",
            "-q",
            "-f",
            str(ROOT / "mkdocs.yml"),
            "-d",
            str(out),
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    yield out


def test_built_page_has_the_live_playground(built_playground: Path) -> None:
    html = (built_playground / "playground" / "index.html").read_text(encoding="utf-8")
    _assert_live_page(html)
    assets = built_playground / "assets" / "playground"
    for name in ("playground.js", "playground.css", "engine.js", "glue.py", "wheel.json"):
        assert (assets / name).exists(), name


def test_the_limits_hint_states_the_engine_limits() -> None:
    engine = (ROOT / "site-playground" / "engine.js").read_text(encoding="utf-8")
    found = re.search(
        r"LIMITS = Object\.freeze\(\{ fileBytes: (\d+) \* MB, "
        r"totalBytes: (\d+) \* MB, files: (\d+) \}\)",
        engine,
    )
    assert found, "engine.js LIMITS changed shape: update this test"
    per_file, total, files = found.groups()
    hint = re.search(
        r"Up to (\d+) files, (\d+)&nbsp;MB each, (\d+)&nbsp;MB in total", PAGE.read_text()
    )
    assert hint, "the limits hint"
    assert hint.groups() == (files, per_file, total)


def test_no_unmeasured_download_size_is_stated() -> None:
    page = PAGE.read_text(encoding="utf-8")
    assert not re.search(r"downloads? (about )?\d+\s*MB", page)


def test_the_documented_table_name_example_is_what_the_glue_does(glue: ModuleType) -> None:
    page = PAGE.read_text(encoding="utf-8")
    found = re.search(r"So `([^`]+)` becomes `([^`]+)`", page)
    assert found, "the table-naming example"
    assert glue.table_name_for(found.group(1), []) == found.group(2)
    assert glue.table_name_for("orders.csv", ["orders"]) == "orders_2"
