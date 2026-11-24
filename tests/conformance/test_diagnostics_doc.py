"""docs/reference/diagnostics.md lists exactly the codes the source emits (spec 12).

The table's code column must equal the set of ``FL\\d{3}`` codes found in ``src/``; each row
names a stage matching the code's range, a severity, a meaning and an example; and the
example of every front-end code (FL1xx-FL3xx) that is written as one claim is linted and
must report that code.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import factlang as fl

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "reference" / "diagnostics.md"
SRC = ROOT / "src"
CODE = re.compile(r"FL\d{3}")
ROW = re.compile(r"^\| (FL\d{3}) \| ([^|]+) \| ([^|]+) \| (.+) \| (.+) \|$", re.M)
STAGES = {
    "1": {"lexer"},
    "2": {"parser", "lexer"},  # FL205 (dangling continuation) is found by the lexer
    "3": {"analyser"},
    "4": {"planning"},
    "5": {"rendering"},
    "6": {"contract"},
    "7": {"execution"},
}
CATALOG = fl.StaticCatalog(
    {
        "orders": {"id": "integer", "customer_id": "integer", "status": "varchar"},
        "customers": {"id": "integer", "email": "varchar"},
    }
)


def _src_codes() -> set[str]:
    return {c for p in SRC.rglob("*.py") for c in CODE.findall(p.read_text(encoding="utf-8"))}


def _rows() -> list[tuple[str, str, str, str, str]]:
    return [tuple(c.strip() for c in m) for m in ROW.findall(DOC.read_text(encoding="utf-8"))]  # type: ignore[misc]


def test_table_lists_exactly_the_codes_in_src() -> None:
    documented = [r[0] for r in _rows()]
    assert len(documented) == len(set(documented)), "a code is listed twice"
    src = _src_codes()
    assert set(documented) == src, (
        f"missing from the doc: {sorted(src - set(documented))}; "
        f"not in src: {sorted(set(documented) - src)}"
    )


def test_codes_mentioned_anywhere_in_the_doc_are_real() -> None:
    assert set(CODE.findall(DOC.read_text(encoding="utf-8"))) <= _src_codes()


@pytest.mark.parametrize("row", _rows(), ids=lambda r: r[0])
def test_row_is_complete(row: tuple[str, str, str, str, str]) -> None:
    code, stage, severity, meaning, example = row
    assert stage in STAGES[code[2]], f"{code}: stage {stage!r}"
    assert severity in {"error", "warning", "info", "none"}, code
    assert meaning and example, code


FRONT_END = [r for r in _rows() if r[0][2] in "123" and re.fullmatch(r"`[^`]+`", r[4])]


def test_front_end_examples_are_mostly_executable() -> None:
    front = [r for r in _rows() if r[0][2] in "123"]
    assert len(FRONT_END) >= len(front) - 2  # FL207 and FL313 are described in prose


@pytest.mark.parametrize("row", FRONT_END, ids=lambda r: r[0])
def test_front_end_example_reports_its_code(row: tuple[str, str, str, str, str]) -> None:
    code, _, severity, _, example = row
    claim = example.strip("`").replace("\\|", "|")
    bag = fl.lint(claim, catalog=CATALOG)
    assert code in bag.codes(), f"{claim!r} -> {bag.codes()}"
    assert any(d.code == code and d.severity.value == severity for d in bag), code
