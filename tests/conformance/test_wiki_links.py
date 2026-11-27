"""The GitHub wiki sources in ``docs/wiki/`` are well formed (plan task 24, spec 14.3).

Every ``[[Page]]`` / ``[[Text|Page]]`` and relative markdown link must resolve to a page file,
every page opens with a three-line summary and ends with a "See also" list, and the page set is
exactly the one spec 14.3 lists. Each check is a function over text with a negative control.
"""

from __future__ import annotations

import hashlib
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


# Names of the project FactLang originated in, stored only as sha256 digests of the lowercased
# word so that the names themselves never appear in the repository (spec 14.3).
FORBIDDEN_DIGESTS = frozenset(
    {
        (9, "da9206a05b870991e535379482bc43dc7a19ff3822b9af0a7a564dae25eb0b3b"),
        (9, "aca236a34cc6f98aa97865cbacff8a30fe393022b29ef36355253cce125a864c"),
    }
)
ROOT = WIKI.parents[1]
SCANNED = ("src", "docs", "tests", ".claude", "README.md", "CHANGELOG.md", "CLAUDE.md")
SCANNED += ("AGENTS.md", "MEMORY.md")
SCANNED += ("site-playground", "scripts", ".github", "mkdocs.yml", "pyproject.toml")
SCANNED += ("CONTRIBUTING.md",)
_WORD = re.compile(r"[a-z0-9]+")


def forbidden_hits(text: str, digests: frozenset[tuple[int, str]] = FORBIDDEN_DIGESTS) -> int:
    """How many lowercased words contain a forbidden name (by digest of every window)."""
    lengths = {n for n, _ in digests}
    hits = 0
    for word in _WORD.findall(text.lower()):
        for n in lengths:
            windows = (word[i : i + n] for i in range(len(word) - n + 1))
            if any((n, hashlib.sha256(w.encode()).hexdigest()) in digests for w in windows):
                hits += 1
                break
    return hits


def _scanned_files() -> list[Path]:
    out: list[Path] = []
    for entry in SCANNED:
        path = ROOT / entry
        if path.is_file():
            out.append(path)
        elif path.is_dir():
            out.extend(
                p for p in sorted(path.rglob("*")) if p.is_file() and "__pycache__" not in p.parts
            )
    return out


def test_no_originating_names() -> None:
    files = _scanned_files()
    assert len(files) > 100  # the scan really covers the tree
    scanned = {p.relative_to(ROOT).parts[0] for p in files}
    # an unpacked sdist has no .github: require each scanned directory that exists
    present = {d for d in ("site-playground", "scripts", ".github") if (ROOT / d).is_dir()}
    assert present <= scanned and "site-playground" in present
    bad = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # binary
            continue
        if forbidden_hits(text):
            bad.append(str(path.relative_to(ROOT)))
    assert bad == []


_HOME = re.compile("/" + r"(?:Users|home)/[A-Za-z0-9._-]+/")  # split: this file is scanned too


def test_no_local_absolute_paths() -> None:
    bad = []
    for path in _scanned_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if _HOME.search(text):
            bad.append(str(path.relative_to(ROOT)))
    assert bad == []


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


def test_control_forbidden_name_is_found_by_digest() -> None:
    digests = frozenset({(6, hashlib.sha256(b"foobar").hexdigest())})
    assert forbidden_hits("A FooBar here, x_foobarbaz there; foo bar no.", digests) == 2
    assert forbidden_hits("nothing to see", digests) == 0


def test_control_local_absolute_path_is_found() -> None:
    assert _HOME.search("venv at " + "/" + "Users/someone/project/.venv")
    assert not _HOME.search("a gitignored .venv at the repository root")
