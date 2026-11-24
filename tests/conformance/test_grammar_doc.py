"""Every example in docs/reference/grammar.md is executable.

FL3xx blocks are syntactically valid and are checked by the analyser against the standard
pack.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from factlang.diagnostics import DiagnosticBag
from factlang.packs.standard import standard_pack
from factlang.semantics.analyzer import analyze
from factlang.syntax import parse

DOC = Path(__file__).resolve().parents[2] / "docs" / "reference" / "grammar.md"
_FENCE = re.compile(
    r"^```(factlang-valid|factlang-invalid)[ \t]*(FL\d{3})?[ \t]*\n(.*?)\n```", re.M | re.S
)


def _blocks() -> list[tuple[str, str | None, str]]:
    return [(m[1], m[2] or None, m[3]) for m in _FENCE.finditer(DOC.read_text(encoding="utf-8"))]


VALID = [(c, s) for kind, c, s in _blocks() if kind == "factlang-valid"]
INVALID = [(c, s) for kind, c, s in _blocks() if kind == "factlang-invalid"]


def test_doc_has_enough_examples() -> None:
    assert len(VALID) >= 25
    assert len(INVALID) >= 20
    assert all(code is not None for code, _ in INVALID)


@pytest.mark.parametrize("src", [s for _, s in VALID], ids=lambda s: s[:50])
def test_valid_blocks_parse_cleanly(src: str) -> None:
    res = parse(src)
    assert not res.diagnostics.has_errors, res.diagnostics.render(src)


@pytest.mark.parametrize(
    ("code", "src"),
    [(c, s) for c, s in INVALID if c is not None and not c.startswith("FL3")],
    ids=lambda v: v[:50],
)
def test_invalid_blocks_report_their_code(code: str, src: str) -> None:
    assert code in parse(src).diagnostics.codes(), src


@pytest.mark.parametrize(
    ("code", "src"),
    [(c, s) for c, s in INVALID if c is not None and c.startswith("FL3")],
    ids=lambda v: v[:50],
)
def test_fl3_blocks_report_their_code(code: str, src: str) -> None:
    res = parse(src)
    assert not res.diagnostics.has_errors, res.diagnostics.render(src)
    bag = DiagnosticBag()
    analyze(res.document, standard_pack().verb_registry(), bag=bag)
    assert code in bag.codes(), src


def test_invalid_blocks_are_not_all_one_code() -> None:
    codes = {c for c, _ in INVALID}
    assert {"FL101", "FL102", "FL201", "FL202", "FL203", "FL204", "FL205", "FL206"} <= codes


def test_fenced_block_pattern_has_a_negative_control() -> None:
    sample = "```factlang-invalid FL203\nrows x\n```"
    assert _FENCE.search(sample) is not None
    assert parse("rows x").diagnostics.codes() == ["FL203"]
    assert parse("ROWS x").diagnostics.codes() == []
