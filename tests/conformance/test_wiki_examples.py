"""Every ```python block of the wiki pages that teach the API runs (plan task 24, review 1).

The blocks of one page share a namespace, in order, against a small DuckDB warehouse
(``conn``, ``connector``, ``checker``). A block preceded by ``<!-- no-test -->`` is skipped; it
may be used only where a real cloud warehouse is required.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

import factlang as fl
from factlang.semantics import types as fl_types

WIKI = Path(__file__).resolve().parents[2] / "docs" / "wiki"
PAGES = ["Extending-Verbs", "Home", "Dialects", "Measurement-API", "Agent-Integration"]
SKIP = "<!-- no-test -->"


def python_blocks(text: str) -> list[tuple[str, bool]]:
    """(code, skipped) for each python fence; a block is skipped when the line before is SKIP."""
    out = []
    for m in re.finditer(r"([^\n]*)\n```python\n(.*?)```", text, re.S):
        out.append((m.group(2), m.group(1).strip() == SKIP))
    return out


def _warehouse() -> Any:
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect()
    conn.execute(
        "CREATE TABLE orders (id INTEGER, customer_id INTEGER, amount DECIMAL(10,2), "
        "status VARCHAR, deleted BOOLEAN)"
    )
    conn.execute(
        "INSERT INTO orders VALUES (1, 10, 5.00, 'paid', NULL), (2, 10, 7.50, 'paid', FALSE), "
        "(3, 11, 2.25, 'open', TRUE)"
    )
    conn.execute("CREATE TABLE customers (id INTEGER)")
    conn.execute("INSERT INTO customers VALUES (10), (11)")
    conn.execute("CREATE TABLE order_items (order_id INTEGER, deleted BOOLEAN)")
    conn.execute("INSERT INTO order_items VALUES (1, NULL), (2, FALSE), (3, NULL)")
    return conn


@pytest.fixture
def registry_guard() -> Any:
    saved = dict(fl_types._REGISTRY)
    yield
    fl_types._REGISTRY.clear()
    fl_types._REGISTRY.update(saved)


@pytest.mark.parametrize("page", PAGES)
def test_python_blocks_run(page: str, registry_guard: Any) -> None:
    blocks = python_blocks((WIKI / f"{page}.md").read_text(encoding="utf-8"))
    assert blocks, f"{page} has no python block"
    conn = _warehouse()
    connector = fl.DBAPIConnector(conn, "duckdb")
    ns: dict[str, Any] = {
        "conn": conn,
        "connector": connector,
        "checker": fl.Checker(connector),
        "__name__": "wiki_example",
    }
    for i, (code, skipped) in enumerate(blocks, 1):
        if skipped:
            continue
        exec(compile(code, f"{page}.md block {i}", "exec"), ns)


def test_skip_marker_is_never_used_on_the_checked_pages() -> None:
    for page in PAGES:
        assert all(not skip for _, skip in python_blocks((WIKI / f"{page}.md").read_text("utf-8")))


# -- negative controls -----------------------------------------------------------------------


def test_control_blocks_and_marker_are_found() -> None:
    text = "a\n```python\nx = 1\n```\n<!-- no-test -->\n```python\nraise SystemExit\n```\n"
    assert python_blocks(text) == [("x = 1\n", False), ("raise SystemExit\n", True)]


def test_control_a_broken_block_fails() -> None:
    with pytest.raises(NameError):
        exec(compile("undefined_name_here", "x", "exec"), {})
