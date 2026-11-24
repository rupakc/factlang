"""The GitHub wiki sources in ``docs/wiki/`` are well formed (plan task 24, spec 14.3).

Every ``[[Page]]`` / ``[[Text|Page]]`` and relative markdown link must resolve to a page file,
every page opens with a three-line summary and ends with a "See also" list, and the page set is
exactly the one spec 14.3 lists. Each check is a function over text with a negative control.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

WIKI = Path(__file__).resolve().parents[2] / "docs" / "wiki"

PAGES = {
    "Home", "Concepts", "Architecture", "Language-Guide", "Lexer", "Parser", "Analyzer", "IR",
    "Dialects", "sqlglot-Bridge", "Optimizer", "Connectors", "Catalogs", "Judge", "Standard-Verbs",
    "Extending-Verbs", "Extending-Dialects", "Extending-Connectors", "Extending-Catalogs",
    "Extending-Packs", "Agent-Integration", "CLI", "Diagnostics", "FAQ", "Glossary",
    "Contributing", "Design-Decisions", "Measurement-API",
}  # fmt: skip
SPECIAL = {"_Sidebar"}

_FENCE = re.compile(r"^```.*?^```", re.S | re.M)
_INLINE = re.compile(r"`[^`\n]*`")
_WIKI_LINK = re.compile(r"\[\[([^\]\n]+)\]\]")
_MD_LINK = re.compile(r"\[[^\]\n]*\]\(([^)\s]+)\)")


def _prose(text: str) -> str:
    return _INLINE.sub("", _FENCE.sub("", text))


def link_targets(text: str) -> list[str]:
    """Page names a document links to: wiki links and relative markdown links."""
    prose = _prose(text)
    targets = [m.split("|")[-1].strip() for m in _WIKI_LINK.findall(prose)]
    for raw in _MD_LINK.findall(prose):
        if re.match(r"^[a-z][a-z0-9+.-]*:", raw) or raw.startswith(("#", "/")):
            continue  # absolute URL, mailto:, anchor or site path: not a wiki page
        targets.append(raw.split("#")[0].removesuffix(".md"))
    return [t for t in targets if t]


def unresolved(text: str, pages: set[str]) -> list[str]:
    return [t for t in link_targets(text) if t not in pages]


def summary_problems(text: str) -> list[str]:
    """A title, then a blockquote of exactly three lines."""
    lines = text.splitlines()
    problems: list[str] = []
    if not lines or not lines[0].startswith("# "):
        return ["first line is not a '# Title'"]
    rest = [ln for ln in lines[1:]]
    while rest and not rest[0].strip():
        rest.pop(0)
    quote: list[str] = []
    while rest and rest[0].startswith(">"):
        quote.append(rest.pop(0))
    if len(quote) != 3:
        problems.append(f"summary has {len(quote)} lines, expected 3")
    return problems


def see_also_problems(text: str) -> list[str]:
    lines = [ln for ln in text.splitlines() if ln.strip()]
    heads = [i for i, ln in enumerate(lines) if ln.strip() == "## See also"]
    if len(heads) != 1:
        return ["needs exactly one '## See also' section"]
    tail = lines[heads[0] + 1 :]
    if not tail or not all(ln.startswith("- [[") for ln in tail):
        return ["'## See also' must be the last section and hold only wiki-link bullets"]
    return []


# -- the real wiki ---------------------------------------------------------------------------


def _files() -> dict[str, Path]:
    return {p.stem: p for p in WIKI.glob("*.md")}


def test_page_set_is_exactly_the_spec_list() -> None:
    assert set(_files()) == PAGES | SPECIAL


@pytest.mark.parametrize("name", sorted(PAGES))
def test_page_has_summary_and_see_also(name: str) -> None:
    text = _files()[name].read_text(encoding="utf-8")
    assert summary_problems(text) == []
    assert see_also_problems(text) == []


def test_every_link_resolves() -> None:
    pages = set(_files())
    bad = {n: unresolved(p.read_text(encoding="utf-8"), pages) for n, p in _files().items()}
    assert {n: t for n, t in bad.items() if t} == {}


def test_sidebar_links_every_page() -> None:
    text = (WIKI / "_Sidebar.md").read_text(encoding="utf-8")
    assert set(link_targets(text)) >= PAGES


def test_architecture_has_ascii_and_mermaid_diagrams() -> None:
    text = (WIKI / "Architecture.md").read_text(encoding="utf-8")
    assert "```mermaid" in text
    assert "ProbePlan" in text and "|  lexer" in text


def test_no_originating_names() -> None:
    forbidden = ("dataguard", "claimlang")
    for name, path in _files().items():
        low = path.read_text(encoding="utf-8").lower()
        assert not [w for w in forbidden if w in low], name


# -- negative controls -----------------------------------------------------------------------


def test_control_broken_wiki_link_is_found() -> None:
    assert unresolved("See [[Nope]] and [[Text|Home]].", {"Home"}) == ["Nope"]


def test_control_broken_relative_link_is_found() -> None:
    assert unresolved("[x](Missing.md) [y](Home) [z](https://e.com) [a](#top)", {"Home"}) == [
        "Missing"
    ]


def test_control_links_in_code_are_ignored() -> None:
    assert link_targets("`[[X]]`\n```\n[[Y]]\n```\n") == []


def test_control_missing_summary_is_found() -> None:
    assert summary_problems("# T\n\n> one\n> two\n\nbody") != []
    assert summary_problems("# T\n\n> a\n> b\n> c\n\nbody") == []


def test_control_missing_see_also_is_found() -> None:
    assert see_also_problems("# T\n\nbody") != []
    assert see_also_problems("## See also\n\n- [[A]]\n\ntext after") != []
    assert see_also_problems("body\n\n## See also\n\n- [[A]]\n") == []
