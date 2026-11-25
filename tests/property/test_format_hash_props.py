"""Spec 0.1.3 F4 / design 13: formatting never changes what a claim means.

For generated *valid* documents (spelled with arbitrary spacing, ``=`` for ``==``,
continuations, comments and blank lines), ``semantic_hash`` of every claim of
``parse(format(x))`` equals that of ``parse(x)``, analysed with the standard pack and
``NullCatalog``.
"""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

import factlang as fl
from factlang.diagnostics import DiagnosticBag
from factlang.semantics.analyzer import analyze
from factlang.semantics.catalog import NullCatalog
from factlang.syntax import parse

_VERBS = fl.standard_pack().verb_registry()
_ws = st.sampled_from([" ", "  ", "\t", " \t "])
_num = st.one_of(
    st.integers(0, 10**6).map(str),
    st.integers(0, 999).flatmap(lambda a: st.integers(0, 99).map(lambda b: f"{a}.{b:02d}")),
)
_pct = st.integers(0, 100).map(lambda n: f"{n}%")
_op = st.sampled_from(["==", "=", ">=", "<=", "<", ">", "!="])
_name = st.sampled_from(["orders", "customers", "order_items", '"Order Lines"', "`events`"])
_col = st.sampled_from(["id", "status", "price", "created_at", '"Total Amt"'])
_filter = st.sampled_from(
    [
        "",
        " WHERE status = 'paid'",
        " WHERE a > 1 AND (b < 2 OR NOT c)",
        " WHERE deleted IS NULL OR NOT deleted",
        " WHERE x IN ['a','b',]",
        " WHERE n IS NULL",
    ]
)


@st.composite
def _claim(draw: st.DrawFn) -> str:
    t, c, op, v, p = draw(_name), draw(_col), draw(_op), draw(_num), draw(_pct)
    w = draw(_ws)
    flt = draw(_filter)
    choice = draw(st.integers(0, 9))
    if choice == 0:
        text = f"ROWS{w}{t}{w}VALUE{w}{op}{w}{v}{flt}"
    elif choice == 1:
        text = f"UNIQUE{w}{t}.{c}{w}MATCH{w}{op}{w}{p}{flt}"
    elif choice == 2:
        text = f"NULLS{w}{t}.{c}{w}RATE{w}{op}{w}{p}{flt}"
    elif choice == 3:
        text = f"MIN{w}{t}.{c}{w}VALUE{w}{op}{w}{v}{w}TOLERANCE{w}{p}{flt}"
    elif choice == 4:
        text = f"MAX{w}{t}.{c}{w}VALUE{w}{op}{w}{v}{w}ABS_TOLERANCE{w}{v}{flt}"
    elif choice == 5:
        text = f"MIN{w}{t}.{c}{w}VALUE{w}{op}{w}DATE '2025-01-31'{flt}"
    elif choice == 6:
        text = f"EXISTS{w}{t}"
    elif choice == 7:
        text = f"FANOUT{w}{t}.{c}{w}MAX_PER_KEY{w}{op}{w}{v}"
    elif choice == 8:
        text = f"JOIN{w}{t}.{c}{w}->{w}customers.id{w}MATCH{w}{op}{w}{p}"
    else:
        text = f"FRESH{w}{t}.{c}{w}WITHIN{w}{draw(st.sampled_from(['90m', '2d', '1w', '7200s']))}"
    if draw(st.booleans()):  # a continuation at the first space (never inside a literal)
        head, sep, tail = text.partition(" ")
        if sep and "'" not in head and '"' not in head:
            text = f"{head} \\{draw(_ws)}\n{draw(_ws)}{tail}"
    if draw(st.booleans()):
        text += f"  # {draw(st.sampled_from(['note', 'a b', 'x = 1']))}"
    return text


@st.composite
def documents(draw: st.DrawFn) -> str:
    lines = draw(st.lists(_claim(), min_size=1, max_size=5))
    out: list[str] = []
    for ln in lines:
        out.extend(draw(st.lists(st.sampled_from(["", "# comment", "  "]), max_size=1)))
        out.append(ln)
    return draw(st.sampled_from(["\n", "\r\n"])).join(out) + "\n"


def _hashes(text: str) -> list[str]:
    result = parse(text)
    assert not result.diagnostics.has_errors, result.diagnostics.render(text)
    bag = DiagnosticBag()
    claims = analyze(result.document, _VERBS, catalog=NullCatalog(), bag=bag)
    return [c.semantic_hash for c in claims]


@settings(max_examples=300, deadline=None)
@given(documents())
def test_format_preserves_every_semantic_hash(text: str) -> None:
    before = _hashes(text)
    assume(before)  # a generated claim the analyser rejects has no hash to compare
    formatted = fl.format(text)
    assert _hashes(formatted) == before
    assert fl.format(formatted) == formatted


def test_generator_produces_analysable_claims() -> None:
    # negative control: the property is not vacuous
    assert len(_hashes("ROWS orders VALUE >= 1\nMIN orders.price VALUE >= 1 TOLERANCE 1%")) == 2
