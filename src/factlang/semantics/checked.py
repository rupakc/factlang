"""The analyser's output: a fully resolved, typed claim (spec 4.6)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from factlang.diagnostics import Span
from factlang.semantics.filters import FilterExpr
from factlang.semantics.refs import ColumnRef, EdgeRef, TableRef


@dataclass(frozen=True)
class CheckedAttr:
    op: str | None
    value: object
    type: str


@dataclass(frozen=True)
class CheckedClaim:
    verb: str
    operands: tuple[TableRef | ColumnRef | EdgeRef, ...]
    attrs: Mapping[str, CheckedAttr]
    filter: FilterExpr | None
    canonical_text: str
    semantic_hash: str
    span: Span
    source_text: str
    comment: str | None

    @property
    def table(self) -> TableRef:
        for o in self.operands:
            if isinstance(o, TableRef):
                return o
            if isinstance(o, ColumnRef):
                return o.table
            return o.left.table
        raise TypeError(f"claim {self.verb} has no operands")

    @property
    def column(self) -> ColumnRef:
        for o in self.operands:
            if isinstance(o, ColumnRef):
                return o
        raise TypeError(f"claim {self.verb} has no column operand")

    @property
    def edge(self) -> EdgeRef:
        for o in self.operands:
            if isinstance(o, EdgeRef):
                return o
        raise TypeError(f"claim {self.verb} has no edge operand")

    def has(self, name: str) -> bool:
        return name in self.attrs

    def value(self, name: str) -> object:
        """Value of attribute ``name``; ``KeyError`` when absent (check ``has`` first)."""
        return self.attrs[name].value

    def op(self, name: str) -> str | None:
        """Comparison operator written on attribute ``name`` (``None`` if bare)."""
        return self.attrs[name].op

    def number(self, name: str) -> Decimal:
        v = self.attrs[name].value
        if isinstance(v, bool) or not isinstance(v, (int, Decimal)):
            raise TypeError(f"attribute {name} is not a number: {v!r}")
        return Decimal(v)
