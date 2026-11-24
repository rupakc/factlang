"""Bindable SQL fragments: a filter or a join path (Amendment A, R6; layer 12).

A :class:`RenderedFragment` is not a statement and FactLang never executes it; it shares the
``{{name}}`` placeholders, :meth:`bind` and :meth:`inline` of
:class:`~factlang.sql.probe.RenderedProbe`. :func:`lower_filter` and :func:`lower_join` build
the internal AST that the printers (and the public ``render_filter``/``render_join``) print.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

from factlang.semantics.filters import FilterExpr, Param
from factlang.semantics.refs import ColumnRef, EdgeRef, TableRef
from factlang.semantics.types import TYPE_FAMILIES
from factlang.sql.ast import Cast, Column, Compare, Expr, FromItem, Join, TableName
from factlang.sql.lower import Lowerer, conj
from factlang.sql.probe import (
    LiteralStyle,
    bind_template,
    check_params,
    check_template,
    inline_template,
    style_for,
)

_ALIAS = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class RenderedFragment:
    """A rendered boolean expression or ``FROM … JOIN …`` clause with typed parameters."""

    sql_template: str
    params: tuple[Param, ...]
    dialect: str
    tier: str

    def __post_init__(self) -> None:
        if isinstance(self.params, list):
            object.__setattr__(self, "params", tuple(self.params))
        check_params(self.params)
        check_template(self.sql_template, self.params)

    def bind(self, paramstyle: str) -> tuple[str, tuple[object, ...] | dict[str, object]]:
        return bind_template(self.sql_template, self.params, paramstyle)

    def inline(
        self, quote_string: Callable[[str], str] | None = None, style: LiteralStyle | None = None
    ) -> str:
        """Display text with literals inlined for ``self.dialect`` (``FL507`` when unsafe)."""
        spelling = style if style is not None else style_for(self.dialect)
        return inline_template(self.sql_template, self.params, quote_string, spelling)


@dataclass(frozen=True)
class JoinEdge:
    """One hop of a join path: positional key tuples (composite allowed), optional CAST."""

    left: tuple[ColumnRef, ...]
    right: tuple[ColumnRef, ...]
    cast_to: str | None = None

    def __post_init__(self) -> None:
        for name in ("left", "right"):
            side = getattr(self, name)
            if isinstance(side, ColumnRef):
                side = (side,)
            if isinstance(side, list):
                side = tuple(side)
            object.__setattr__(self, name, side)
            if not isinstance(side, tuple) or not side:
                raise ValueError(f"JoinEdge.{name} must be a non-empty tuple of ColumnRefs")
            if not all(isinstance(c, ColumnRef) for c in side):
                raise TypeError(f"JoinEdge.{name} must hold ColumnRefs")
            if len({c.table for c in side}) != 1:
                raise ValueError(f"JoinEdge.{name} columns must all be on one table")
        if len(self.left) != len(self.right):
            raise ValueError("JoinEdge key length mismatch")
        if self.cast_to is not None and self.cast_to not in TYPE_FAMILIES:
            raise ValueError(f"unknown cast type family {self.cast_to!r}")

    @property
    def left_table(self) -> TableRef:
        return self.left[0].table

    @property
    def right_table(self) -> TableRef:
        return self.right[0].table


def lower_filter(filter: FilterExpr, alias: str | None = None, *, param_prefix: str = "p") -> Expr:
    """A resolved filter as a boolean AST expression, columns qualified with ``alias``.

    Parameters are named ``<param_prefix>1``, ``<param_prefix>2``, …; a host combining
    several fragments in one statement gives each a different prefix.
    """
    if alias is not None and not _ALIAS.fullmatch(alias):
        raise ValueError(f"alias {alias!r} is not a plain identifier")
    return Lowerer(param_prefix).filter(filter, alias)


def lower_join(
    edges: Sequence[EdgeRef | JoinEdge],
    filters: Mapping[TableRef, FilterExpr] | None = None,
    aliases: Mapping[TableRef, str] | None = None,
    *,
    param_prefix: str = "p",
) -> tuple[FromItem, Expr | None]:
    """``FROM a JOIN b ON … [JOIN c ON …]`` for a path of edges, plus the WHERE part.

    The first edge's left table is the left-most table; each later edge must connect one
    table already in the path to one new table (either direction). The left-most table's
    filter is returned as the WHERE part; every other table's filter is ANDed into the
    ``ON`` of the join that brings it in. Tables without an alias get ``t1``, ``t2``, … in
    path order. Joins are INNER. Parameters are named ``<param_prefix>1``, …. Self-joins
    (an edge from a table to itself) are not supported in v0.1.
    """
    hops = [JoinEdge((e.left,), (e.right,)) if isinstance(e, EdgeRef) else e for e in edges]
    if not hops:
        raise ValueError("a join path needs at least one edge")
    for e in hops:
        if not isinstance(e, JoinEdge):
            raise TypeError(f"edges must be EdgeRef or JoinEdge, got {e!r}")
    given = dict(aliases or {})
    filters = dict(filters or {})
    lowerer = Lowerer(param_prefix)
    for i, e in enumerate(hops):
        if e.left_table == e.right_table:
            raise ValueError(f"join edge {i + 1}: self-joins are not supported in v0.1")

    order: list[TableRef] = [hops[0].left_table]
    for i, e in enumerate(hops):
        lt, rt = e.left_table, e.right_table
        seen_l, seen_r = lt in order, rt in order
        if seen_l and seen_r:
            raise ValueError(f"join edge {i + 1} revisits a table already in the path")
        if not seen_l and not seen_r:
            raise ValueError(f"join edge {i + 1} is not connected to the path")
        order.append(rt if seen_l else lt)
    names: dict[TableRef, str] = {}
    for n, t in enumerate(order, start=1):
        names[t] = given.get(t, f"t{n}")
    for t, a in names.items():
        if not _ALIAS.fullmatch(a):
            raise ValueError(f"alias {a!r} for {t.table.text!r} is not a plain identifier")
    if len(set(names.values())) != len(names):
        raise ValueError(f"duplicate alias in join path: {sorted(names.values())}")
    stray = [t for t in filters if t not in names]
    if stray:
        raise ValueError(f"filter for table {stray[0].table.text!r} not in the join path")

    def key(c: ColumnRef, cast_to: str | None) -> Expr:
        col = Column(c.column, names[c.table])
        return Cast(col, cast_to) if cast_to is not None else col

    first = order[0]
    where = lowerer.filter(filters[first], names[first]) if first in filters else None
    from_: FromItem = TableName(first, alias=names[first])
    for e, new in zip(hops, order[1:], strict=True):
        eqs = [
            Compare("=", key(lc, e.cast_to), key(rc, e.cast_to))
            for lc, rc in zip(e.left, e.right, strict=True)
        ]
        extra = lowerer.filter(filters[new], names[new]) if new in filters else None
        on = conj(*eqs, extra)
        assert on is not None
        from_ = Join(from_, TableName(new, alias=names[new]), on, "INNER")
    return from_, where


__all__ = ["JoinEdge", "RenderedFragment", "lower_filter", "lower_join"]
