"""Property: any string without a backslash or NUL inlines to a literal that parses back to
the same value (0.1.1 hardening)."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from factlang.engine import string_quoter
from factlang.ir import Arity, ColumnAggregate, Metric
from factlang.semantics.filters import Param
from factlang.semantics.refs import Ident, TableRef
from factlang.sql.probe import RenderedProbe

sqlglot = pytest.importorskip("sqlglot")
from sqlglot import exp  # noqa: E402

from factlang.sql.registry import SQLGLOT_NAMES  # noqa: E402

PLAN = ColumnAggregate(TableRef(None, None, Ident("orders")), None, (Metric.ROW_COUNT,))
SAFE_TEXT = st.text(st.characters(blacklist_characters="\\\x00"), max_size=60)


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "snowflake", "duckdb"])
@settings(max_examples=300, deadline=None)
@given(text=SAFE_TEXT)
def test_safe_strings_round_trip_through_sqlglot(dialect: str, text: str) -> None:
    p = RenderedProbe(
        "SELECT {{p1}} AS x", (Param("p1", text, "string"),), Arity.SINGLE_ROW, PLAN, dialect, "v"
    )
    sql = p.inline(string_quoter(dialect))
    tree = sqlglot.parse_one(sql, read=SQLGLOT_NAMES[dialect])
    node = tree.expressions[0].this
    assert isinstance(node, exp.Literal) and node.is_string, sql
    assert node.this == text


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
