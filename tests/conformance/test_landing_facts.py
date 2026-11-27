"""Every fact the landing page states is the code's (site spec 2 G4, 5.1, 6).

- ``scripts/docs_hooks.site_facts()`` equals values recomputed here from the code and
  ``pyproject.toml`` by a different route, and ``on_config`` hands it to the template.
- ``docs/overrides/home.html`` takes its numbers and dialect lists from those facts, never
  from literals.
- The worked examples on the landing page are real: the claims in "The idea" and the
  artefacts of "How a claim becomes a verdict" are recomputed on the getting-started shop,
  every ``fl-src`` claim lints, and every measured verb example compiles to one ``SELECT``
  (the read-only wording).
- The quick start is the getting-started shop, and its printed output is what it prints.
- After ``mkdocs build``, the rendered page shows each number in the stat strip, each
  dialect in its tier group, and every link on it resolves to a built page.
"""

from __future__ import annotations

import contextlib
import dataclasses
import html
import importlib.metadata
import importlib.util
import inspect
import io
import os
import re
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest

import factlang as fl
from factlang.sql import registry as sql_registry

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"
HOOKS = ROOT / "scripts" / "docs_hooks.py"
HOME = DOCS / "overrides" / "home.html"
INDEX = DOCS / "index.md"

_PYTHON = re.compile(r"^```python[ \t]*\n(.*?)^```", re.M | re.S)
_TEXT = re.compile(r"^```text[ \t]*\n(.*?)^```", re.M | re.S)

STAT_KEYS = ("native_dialects", "verbs", "verdicts", "core_dependencies")
# numbers rendered from the facts outside the stat strip (the agents card)
CARD_FACTS = {
    "agent_tools": lambda f: str(len(f["agent_tools"])),
    "max_claims": lambda f: str(f["max_claims"]),
}


def _hooks() -> ModuleType:
    if not HOOKS.exists():  # an unpacked sdist carries the docs, not the site build hooks
        pytest.skip("scripts/docs_hooks.py is not part of the sdist")
    spec = importlib.util.spec_from_file_location("docs_hooks", HOOKS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _home() -> str:
    return HOME.read_text(encoding="utf-8")


def _shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Run the getting-started shop (its first python block); returns its namespace."""
    pytest.importorskip("duckdb")
    page = (DOCS / "getting-started.md").read_text(encoding="utf-8")
    ns: dict[str, Any] = {}
    monkeypatch.chdir(tmp_path)
    exec(compile(_PYTHON.findall(page)[0], "<getting-started block 1>", "exec"), ns)
    return ns


def _core_dependencies() -> list[str]:
    """Core requirements, from pyproject.toml when tomllib exists, else from the metadata."""
    try:
        import tomllib
    except ModuleNotFoundError:  # Python 3.10
        reqs = importlib.metadata.requires("factlang") or []
        return [r for r in reqs if "extra ==" not in r]
    with (ROOT / "pyproject.toml").open("rb") as fh:
        return list(tomllib.load(fh)["project"]["dependencies"])


# ── site_facts() is the code ──────────────────────────────────────────────────────────


def test_site_facts_are_the_code() -> None:
    facts = _hooks().site_facts()
    assert facts["version"] == fl.__version__
    assert facts["native_dialects"] == len(fl.sql.native_dialects())
    assert facts["verbs"] == len(fl.standard_pack().names())
    assert facts["verdicts"] == len(fl.Verdict)
    assert facts["core_dependencies"] == len(_core_dependencies())
    expected: dict[str, list[str]] = {}
    for name in fl.sql.native_dialects():
        profile = sql_registry.get_profile(name)
        assert profile is not None
        expected.setdefault(profile.tier, []).append(name)
    assert facts["tiers"] == {
        "verified": sorted(expected.pop("verified", [])),
        "native": sorted(expected.pop("native", [])),
    }
    assert expected == {}, f"a native profile has a tier the landing page does not show: {expected}"
    # the agents card: tool names and the default claim cap, read here by another route
    from factlang.agent import TOOL_NAMES, handle_tool_call

    assert facts["agent_tools"] == [n.removeprefix("factlang_") for n in TOOL_NAMES]
    default_cap = inspect.signature(handle_tool_call).parameters["max_claims"].default
    assert facts["max_claims"] == default_cap


def test_site_facts_count_a_new_dialect() -> None:
    """Negative control: the facts follow the registry, they are not frozen numbers."""
    hooks = _hooks()
    before = hooks.site_facts()
    duckdb = sql_registry.get_profile("duckdb")
    assert duckdb is not None
    extra = dataclasses.replace(duckdb, name="zz_test", tier="native")
    sql_registry.register_profile(extra)
    try:
        after = hooks.site_facts()
    finally:
        sql_registry._PROFILES.pop("zz_test", None)
    assert after["native_dialects"] == before["native_dialects"] + 1
    assert "zz_test" in after["tiers"]["native"]


def test_on_config_hands_the_facts_to_the_template() -> None:
    hooks = _hooks()
    config: dict[str, Any] = {"extra": {}}
    out = hooks.on_config(config)
    assert out is config
    assert config["extra"]["fl_facts"] == hooks.site_facts()


def test_template_reads_numbers_and_dialects_from_the_facts() -> None:
    home = _home()
    for key in (*STAT_KEYS, "version"):
        assert re.search(r"facts\." + key + r"\b", home), f"home.html does not render {key}"
    assert re.search(r"facts\.tiers\b", home)
    # every stat is a data-fact element filled by Jinja, never a typed number
    stats = re.findall(r'data-fact="(\w+)"[^>]*>([^<]*)<', home)
    assert sorted(k for k, _ in stats) == sorted((*STAT_KEYS, *CARD_FACTS))
    for key, body in stats:
        assert "{{" in body and not re.search(r"\d", body.replace("facts.", "")), (key, body)
    for typed in ("Three tools", "(200 by default)", "200 by default"):
        assert typed not in home, f"{typed!r} is typed in; render it from the facts"
    # no dialect name is written into a tier list by hand
    tier_markup = "".join(re.findall(r'data-tier="(?:verified|native)".*?</ul>', home, re.S))
    for name in fl.sql.native_dialects():
        assert name not in tier_markup, f"{name} is hard-coded in a tier group"


# ── the worked examples are real ──────────────────────────────────────────────────────


def test_every_landing_claim_lints() -> None:
    claims = re.findall(r'<code class="[^"]*\bfl-src\b[^"]*"[^>]*>(.*?)</code>', _home(), re.S)
    assert len(claims) >= 10
    for raw in claims:
        text = html.unescape(re.sub(r"<[^>]+>", "", raw))
        bag = fl.lint(text)
        assert not bag.has_errors, f"{text}\n{bag.render()}"


def test_the_idea_shows_real_outcomes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ns = _shop(tmp_path, monkeypatch)
    home = _home()
    block = re.search(r'<code class="fl-src" data-idea-claims>(.*?)</code>', home, re.S)
    assert block is not None
    claims = [c for c in html.unescape(block.group(1)).splitlines() if c.strip()]
    results = re.findall(
        r'<li data-verdict="(\w+)">.*?<span class="fl-idea__why">(.*?)</span>', home, re.S
    )
    assert len(claims) == len(results) == 3
    checker = fl.Checker(ns["connector"])
    for claim, (verdict, why) in zip(claims, results, strict=True):
        (outcome,) = checker.check(claim).outcomes
        assert outcome.verdict.value == verdict, claim
        assert outcome.explanation == html.unescape(why), claim
    assert "refunded" in home  # the most common undeclared status, named in the prose
    (values,) = checker.check(claims[2]).outcomes
    assert values.facts["top_undeclared"][0]["value"] == "refunded"


def _artefacts() -> dict[str, str]:
    found = re.findall(r'data-artefact="(\w+)">(.*?)</code>', _home(), re.S)
    return {k: html.unescape(re.sub(r"<[^>]+>", "", v)).strip() for k, v in found}


def test_pipeline_artefacts_are_real(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ns = _shop(tmp_path, monkeypatch)
    art = _artefacts()
    assert list(art) == ["claim", "parse", "analyse", "plan", "compile", "params", "run", "judge"]
    claim = art["claim"]
    # parse: the canonical spelling fl.format prints
    assert art["parse"] == fl.format(claim).strip()
    # analyse: the semantic hash (shown shortened with an ellipsis)
    (outcome,) = fl.Checker(ns["connector"]).check(claim).outcomes
    shown = art["analyse"]
    assert shown.endswith("…") and len(shown) > len("sha256:") + 8
    assert outcome.semantic_hash.startswith(shown[:-1])
    # plan: the IR node, its metric and its filter
    (probe,) = fl.compile(claim, dialect="duckdb")
    plan: Any = probe.plan
    flt = plan.filter
    rendered = (
        f"{type(plan).__name__}({plan.table.table.text}, "
        f"{', '.join(m.value for m in plan.metrics)}, "
        f"WHERE {flt.column.text} {flt.op} {flt.param.name})"
    )
    assert art["plan"] == rendered
    # compile: the executable statement and its bind parameters
    sql, params = probe.bind("qmark")
    assert probe.dialect == "duckdb" and probe.tier == "verified"
    assert art["compile"] == sql
    assert art["params"] == repr(tuple(params))
    # run: the row DuckDB returns
    cursor = ns["conn"].execute(sql, list(params))
    names = [d[0] for d in cursor.description]
    (row,) = cursor.fetchall()
    assert art["run"] == ", ".join(f"{n} {v}" for n, v in zip(names, row, strict=True))
    # judge: the verdict and its explanation
    assert art["judge"] == f"{outcome.verdict.value} · {outcome.explanation}"


def test_every_measured_claim_is_one_select() -> None:
    """Backs "every measured claim compiles to one SELECT" (NOTE is never measured)."""
    pack = fl.standard_pack()
    seen: dict[str, int] = {}
    for name in pack.names():
        for example in pack.get(name).examples:
            if not example.valid or example.expect:
                continue
            probes = list(fl.compile(example.source, dialect="duckdb"))
            if name == "NOTE":
                assert probes == []
                continue
            assert len(probes) == 1, example.source
            assert probes[0].sql_template.lstrip().upper().startswith("SELECT"), example.source
            seen[name] = seen.get(name, 0) + 1
    missing = [n for n in pack.names() if n != "NOTE" and not seen.get(n)]
    assert not missing, f"verbs with no valid example compiled here: {missing}"


def test_best_effort_examples_are_sqlglot_dialects() -> None:
    sqlglot = pytest.importorskip("sqlglot")
    names = re.findall(r'data-bridge-dialect="(\w+)"', _home())
    assert names
    for name in names:
        assert name not in fl.sql.native_dialects()
        sqlglot.Dialect.get_or_raise(name)


# ── the quick start ───────────────────────────────────────────────────────────────────


def test_quick_start_is_the_getting_started_shop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("duckdb")
    index = INDEX.read_text(encoding="utf-8")
    start = (DOCS / "getting-started.md").read_text(encoding="utf-8")
    blocks = _PYTHON.findall(index)
    assert len(blocks) == 2, "the quick start has a build step and a check step"
    assert blocks[0] == _PYTHON.findall(start)[0]
    assert 'pip install "factlang[duckdb]"' in index
    (expected,) = _TEXT.findall(index)
    ns: dict[str, Any] = {}
    monkeypatch.chdir(tmp_path)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        for i, block in enumerate(blocks, start=1):
            exec(compile(block, f"<index block {i}>", "exec"), ns)
    assert out.getvalue() == expected


# ── the built page ────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def built_site(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    if importlib.util.find_spec("mkdocs") is None or importlib.util.find_spec("material") is None:
        pytest.skip('mkdocs is not installed: pip install -e ".[docs]" to build the site')
    _hooks()  # skips in an sdist
    out = tmp_path_factory.mktemp("site")
    env = {**os.environ, "NO_MKDOCS_2_WARNING": "1"}
    src = str(ROOT / "src")
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
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


def test_built_page_shows_the_facts(built_site: Path) -> None:
    page = (built_site / "index.html").read_text(encoding="utf-8")
    facts = _hooks().site_facts()
    stats = dict(re.findall(r'data-fact="(\w+)"[^>]*>\s*([^<]*?)\s*<', page))
    expected = {k: str(facts[k]) for k in STAT_KEYS}
    expected.update({k: render(facts) for k, render in CARD_FACTS.items()})
    assert stats == expected
    for tier in ("verified", "native"):
        group = re.search(r'data-tier="' + tier + r'".*?</ul>', page, re.S)
        assert group is not None, tier
        shown = re.findall(r'data-dialect="(\w+)"', group.group(0))
        assert shown == facts["tiers"][tier], tier
    assert facts["version"] in page
    assert "fl-ledger__row" in page and "fl-src" in page


def test_every_landing_link_resolves(built_site: Path) -> None:
    page = (built_site / "index.html").read_text(encoding="utf-8")
    hrefs = set(re.findall(r'href="([^"]+)"', page))
    checked = 0
    for href in sorted(hrefs):
        parts = urlsplit(href)
        if parts.scheme or href.startswith(("//", "mailto:")):
            continue
        path = unquote(parts.path)
        target = (built_site / path).resolve()
        if target.is_dir() or path.endswith("/"):
            target = target / "index.html"
        assert target.exists(), f"{href} does not resolve to a built page"
        if parts.fragment and target.suffix == ".html":
            body = target.read_text(encoding="utf-8")
            assert f'id="{parts.fragment}"' in body, f"{href}: no anchor #{parts.fragment}"
        checked += 1
    assert checked >= 15
    for page_link in (
        "playground/",
        "getting-started/",
        "agents/",
        "cli/",
        "guide/batching/",
        "guide/evidence/",
        "guide/verdicts/",
        "guide/verbs/",
        "reference/diagnostics/",
        "extending/verbs/",
    ):
        assert any(h.split("#")[0].endswith(page_link) for h in hrefs), page_link


def test_shop_numbers_in_the_prose_are_the_shop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The numbers typed into sentences (quick start, idea panel) are the shop's."""
    ns = _shop(tmp_path, monkeypatch)
    conn = ns["conn"]
    (customers,) = conn.execute("SELECT COUNT(*) FROM customers").fetchone()
    (orders,) = conn.execute("SELECT COUNT(*) FROM orders").fetchone()
    index = INDEX.read_text(encoding="utf-8")
    assert f"{customers:,} customers and {orders:,} orders" in index
    (paid,) = (
        fl.Checker(ns["connector"])
        .check("COUNT orders VALUE ==1400 WHERE status = 'paid'")
        .outcomes
    )
    assert f"<mark>{paid.facts['value']:,} paid orders</mark>" in _home()
