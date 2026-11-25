"""Property: any string without a backslash, NUL, line terminator or lone surrogate inlines
to a literal that parses back to the same value, in every native dialect and the bridged
Hive family, Presto and Trino; where the dialect's lexer has no doubled-quote form a string
holding ``'`` is refused with ``FL507`` instead (0.1.1 hardening, post-release review). A
string holding a line terminator is refused in every dialect (0.1.2)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factlang.engine import string_quoter
from factlang.ir import Arity, ColumnAggregate, Metric
from factlang.semantics.filters import Param
from factlang.semantics.refs import Ident, TableRef
from factlang.sql import native_dialects
from factlang.sql.probe import RenderedProbe, RenderError

sqlglot = pytest.importorskip("sqlglot")
from sqlglot import exp  # noqa: E402

from factlang.sql.registry import SQLGLOT_NAMES  # noqa: E402

PLAN = ColumnAggregate(TableRef(None, None, Ident("orders")), None, (Metric.ROW_COUNT,))
LINE_TERMINATORS = "\r\n\x85\u2028\u2029"
SAFE_TEXT = st.text(
    st.characters(blacklist_characters="\\\x00" + LINE_TERMINATORS, blacklist_categories=("Cs",)),
    max_size=60,
)
NO_DOUBLED_QUOTE = {"bigquery", "databricks", "spark", "spark2", "hive"}
ROUND_TRIP_DIALECTS = [*native_dialects(), "spark", "spark2", "hive", "presto", "trino"]


@pytest.mark.parametrize("dialect", sorted(set(ROUND_TRIP_DIALECTS)))
@settings(max_examples=300, deadline=None)
@given(text=SAFE_TEXT | st.sampled_from(["it's", "''", "x' OR 1=1 --"]))
def test_safe_strings_round_trip_through_sqlglot(dialect: str, text: str) -> None:
    p = RenderedProbe(
        "SELECT {{p1}} AS x", (Param("p1", text, "string"),), Arity.SINGLE_ROW, PLAN, dialect, "v"
    )
    if dialect in NO_DOUBLED_QUOTE and "'" in text:
        with pytest.raises(RenderError, match=r"^FL507"):
            p.inline(string_quoter(dialect))
        return
    sql = p.inline(string_quoter(dialect))
    tree = sqlglot.parse_one(sql, read=SQLGLOT_NAMES.get(dialect, dialect))
    node = tree.expressions[0].this
    if dialect == "tsql":
        assert isinstance(node, exp.National), sql
    else:
        assert isinstance(node, exp.Literal) and node.is_string, sql
    assert node.this == text


@pytest.mark.parametrize("dialect", sorted(set(ROUND_TRIP_DIALECTS)))
@settings(max_examples=100, deadline=None)
@given(
    head=SAFE_TEXT,
    terminator=st.sampled_from(sorted(LINE_TERMINATORS)),
    tail=SAFE_TEXT,
)
def test_a_line_terminator_is_refused_everywhere(
    dialect: str, head: str, terminator: str, tail: str
) -> None:
    text = head + terminator + tail
    p = RenderedProbe(
        "SELECT {{p1}} AS x", (Param("p1", text, "string"),), Arity.SINGLE_ROW, PLAN, dialect, "v"
    )
    with pytest.raises(RenderError, match=r"^FL507"):
        p.inline(string_quoter(dialect))
    with pytest.raises(RenderError, match=r"^FL507"):
        p.inline(lambda s: "'" + s.replace("'", "''") + "'")


@pytest.mark.parametrize("dialect", sorted(SQLGLOT_NAMES))
@settings(max_examples=200, deadline=None)
@given(value=st.floats(allow_nan=False, allow_infinity=False))
def test_finite_floats_round_trip_through_sqlglot(dialect: str, value: float) -> None:
    p = RenderedProbe(
        "SELECT {{p1}} AS x", (Param("p1", value, "float"),), Arity.SINGLE_ROW, PLAN, dialect, "v"
    )
    tree = sqlglot.parse_one(p.inline(string_quoter(dialect)), read=SQLGLOT_NAMES[dialect])
    node = tree.expressions[0].this
    sign = 1.0
    if isinstance(node, exp.Neg):
        sign, node = -1.0, node.this
    assert isinstance(node, exp.Literal) and not node.is_string
    assert sign * float(node.this) == value
