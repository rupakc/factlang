"""Resolved filter IR and its canonical text (spec 4.5, 4.6).

``canonical`` is deterministic and independent of ``Param.name``: a parameter is rendered from
its family and value. Commutative nodes (``FAnd``/``FOr``) sort their items by canonical text and
``FIn`` sorts its parameters, so ``a = 1 AND b = 2`` and ``b = 2 AND a = 1`` agree.
"""

from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass
from decimal import Decimal

from factlang.semantics.refs import Ident
from factlang.syntax.formatter import format_number

PARAM_FAMILIES: tuple[str, ...] = (
    "integer",
    "decimal",
    "float",
    "string",
    "boolean",
    "date",
    "timestamp",
)
COMPARE_OPS: tuple[str, ...] = ("=", "!=", "<", "<=", ">", ">=")


@dataclass(frozen=True)
class Param:
    name: str
    value: object
    family: str


@dataclass(frozen=True)
class FCompare:
    column: Ident
    op: str
    param: Param

    def __post_init__(self) -> None:
        if self.op == "==":
            object.__setattr__(self, "op", "=")
        if self.op not in COMPARE_OPS:
            raise ValueError(f"unknown comparison operator {self.op!r}")


@dataclass(frozen=True)
class FBool:
    """Bare boolean atom (R9); canonically ``column = TRUE``."""

    column: Ident


@dataclass(frozen=True)
class FIn:
    column: Ident
    params: tuple[Param, ...]
    negated: bool = False


@dataclass(frozen=True)
class FIsNull:
    column: Ident
    negated: bool = False


@dataclass(frozen=True)
class FBetween:
    column: Ident
    low: Param
    high: Param


@dataclass(frozen=True)
class FAnd:
    items: tuple[FilterExpr, ...]


@dataclass(frozen=True)
class FOr:
    items: tuple[FilterExpr, ...]


@dataclass(frozen=True)
class FNot:
    item: FilterExpr


FilterExpr = FCompare | FBool | FIn | FIsNull | FBetween | FAnd | FOr | FNot


def _value_text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format_number(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    return json.dumps(str(value), ensure_ascii=False)


def _param(p: Param) -> str:
    return f"{p.family}:{_value_text(p.value)}"


def _col(c: Ident) -> str:
    if c.quoted:
        return '"' + c.text.replace('"', '""') + '"'
    return c.text.lower()


def canonical(expr: FilterExpr) -> str:
    """Stable text of ``expr`` used by the semantic hash."""
    if isinstance(expr, FCompare):
        return f"({_col(expr.column)} {expr.op} {_param(expr.param)})"
    if isinstance(expr, FBool):
        return f"({_col(expr.column)} = {_param(Param('', True, 'boolean'))})"
    if isinstance(expr, FIn):
        items = ",".join(sorted(_param(p) for p in expr.params))
        word = "NOT IN" if expr.negated else "IN"
        return f"({_col(expr.column)} {word} [{items}])"
    if isinstance(expr, FIsNull):
        word = "IS NOT NULL" if expr.negated else "IS NULL"
        return f"({_col(expr.column)} {word})"
    if isinstance(expr, FBetween):
        return f"({_col(expr.column)} BETWEEN {_param(expr.low)} {_param(expr.high)})"
    if isinstance(expr, FAnd):
        return "AND[" + ",".join(sorted(canonical(i) for i in expr.items)) + "]"
    if isinstance(expr, FOr):
        return "OR[" + ",".join(sorted(canonical(i) for i in expr.items)) + "]"
    return f"NOT[{canonical(expr.item)}]"
