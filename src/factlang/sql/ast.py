"""Internal, dialect-free SQL AST (spec 8.2, layer 12).

Every primitive is lowered into these nodes by :mod:`factlang.sql.lower`; the native printer
and the sqlglot bridge both start from this one tree, so they cannot disagree on semantics.

Safety rules the tree itself enforces:

- every user value is a :class:`ParamRef` (a typed bind parameter, spec 8.4);
- :class:`Literal` holds only the handful of constants FactLang itself writes
  (``LITERAL_CONSTANTS`` and ``NULL``), so no user text can reach the SQL template;
- sampling exists only as explicit nodes (:class:`TableSample` on a FROM item,
  :class:`KeyHash` in a ``WHERE`` predicate) built from an explicit ``SampleSpec``.

Notes for printers:

- ``Agg("COUNT", None)`` is ``COUNT(*)``.
- :class:`CountIf` is the number of rows where ``cond`` is TRUE, and is ``0`` (never NULL)
  over no rows: a printer spelling it ``SUM(CASE WHEN … THEN 1 ELSE 0 END)`` must wrap it
  in ``COALESCE(…, 0)``.
- :class:`KeyHash` is the keyhash *bucket* in ``[0, 1_000_000)`` of spec 19 (keyhash
  ruling): the hash input is ``seed || '|' || part1 [|| '|' || part2 …]``; the parts are
  already ``Cast(…, "string")`` and NULL parts are already excluded by the lowering.
- A :class:`UnionAll` branch that has ``order_by`` or ``limit`` must be printed
  parenthesised (``(SELECT … ORDER BY … LIMIT n) UNION ALL …``), or as a derived table
  where the dialect cannot parenthesise a branch (e.g. ``TOP`` in T-SQL); otherwise the
  ORDER BY/LIMIT would bind to the whole union.
- :class:`CatalogColumns` is the relation ``(column_name, data_type)`` of one table's
  columns; the printer spells it from its profile's ``information_schema`` template and
  binds the table/column names as string parameters (after its identifier-case rule).
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal

from factlang.semantics.filters import Param
from factlang.semantics.refs import Ident, TableRef
from factlang.semantics.types import TYPE_FAMILIES
from factlang.sql.probe import RenderError

AGG_FUNCTIONS: tuple[str, ...] = ("COUNT", "SUM", "AVG", "MIN", "MAX")
COMPARE_OPS: tuple[str, ...] = ("=", "!=", "<", "<=", ">", ">=")
JOIN_KINDS: tuple[str, ...] = ("LEFT", "INNER", "CROSS")
TABLE_SAMPLE_METHODS: tuple[str, ...] = ("system", "bernoulli")
# The only constants FactLang writes into SQL text itself (plus NULL).
LITERAL_CONSTANTS: frozenset[object] = frozenset({0, 1, "summary", "undeclared", 1_000_000})


def _set(obj: object, name: str, value: object) -> None:
    object.__setattr__(obj, name, value)


def _tuple(obj: object, name: str) -> tuple[object, ...]:
    value = getattr(obj, name)
    if isinstance(value, list):
        value = tuple(value)
        _set(obj, name, value)
    if not isinstance(value, tuple):
        raise TypeError(f"{type(obj).__name__}.{name} must be a tuple, got {value!r}")
    return value


# --- expressions -------------------------------------------------------------------------


@dataclass(frozen=True)
class Column:
    """A column, optionally qualified by a FactLang-chosen (or caller-given) alias."""

    name: Ident
    qualifier: str | None = None


@dataclass(frozen=True)
class ParamRef:
    """A typed bind parameter: the only way a user value enters a statement."""

    param: Param


@dataclass(frozen=True)
class Literal:
    """A constant FactLang itself writes (0, 1, 'summary', 'undeclared', 1000000) or NULL."""

    value: object

    def __post_init__(self) -> None:
        v = self.value
        if v is None:
            return
        if isinstance(v, bool) or not isinstance(v, (int, str)) or v not in LITERAL_CONSTANTS:
            raise ValueError(
                f"Literal {v!r} is not a FactLang constant; user values must be ParamRefs"
            )


@dataclass(frozen=True)
class Star:
    """``*`` in a select list."""


@dataclass(frozen=True)
class Agg:
    fn: str
    arg: Expr | None = None
    distinct: bool = False

    def __post_init__(self) -> None:
        if self.fn not in AGG_FUNCTIONS:
            raise ValueError(f"unknown aggregate {self.fn!r}; expected one of {AGG_FUNCTIONS}")
        if self.arg is None and (self.fn != "COUNT" or self.distinct):
            raise ValueError("only COUNT(*) may omit its argument")


@dataclass(frozen=True)
class CountIf:
    cond: Expr


@dataclass(frozen=True)
class Lower:
    e: Expr


@dataclass(frozen=True)
class Trim:
    e: Expr


@dataclass(frozen=True)
class SplitPrefix:
    """The part of ``e`` before the first occurrence of ``separator`` (all of ``e`` if none)."""

    e: Expr
    separator: ParamRef


@dataclass(frozen=True)
class Like:
    e: Expr
    pattern: ParamRef


@dataclass(frozen=True)
class Cast:
    e: Expr
    family: str

    def __post_init__(self) -> None:
        if self.family not in TYPE_FAMILIES:
            raise ValueError(f"unknown cast type family {self.family!r}")


@dataclass(frozen=True, init=False)
class Coalesce:
    items: tuple[Expr, ...]

    def __init__(self, *items: Expr) -> None:
        if len(items) < 2:
            raise ValueError("COALESCE needs at least two arguments")
        _set(self, "items", tuple(items))


@dataclass(frozen=True)
class Case:
    whens: tuple[tuple[Expr, Expr], ...]
    else_: Expr | None = None

    def __post_init__(self) -> None:
        whens = _tuple(self, "whens")
        if not whens:
            raise ValueError("CASE needs at least one WHEN")
        for w in whens:
            if not isinstance(w, tuple) or len(w) != 2:
                raise TypeError(f"CASE whens must be (condition, result) pairs, got {w!r}")


@dataclass(frozen=True)
class Now:
    """The server's current timestamp."""


@dataclass(frozen=True)
class Compare:
    op: str
    left: Expr
    right: Expr

    def __post_init__(self) -> None:
        if self.op not in COMPARE_OPS:
            raise ValueError(f"unknown comparison operator {self.op!r}")


@dataclass(frozen=True)
class And:
    items: tuple[Expr, ...]

    def __post_init__(self) -> None:
        if len(_tuple(self, "items")) < 2:
            raise ValueError("And needs at least two items")


@dataclass(frozen=True)
class Or:
    items: tuple[Expr, ...]

    def __post_init__(self) -> None:
        if len(_tuple(self, "items")) < 2:
            raise ValueError("Or needs at least two items")


@dataclass(frozen=True)
class Not:
    e: Expr


@dataclass(frozen=True)
class InList:
    e: Expr
    items: tuple[Expr, ...]
    negated: bool = False

    def __post_init__(self) -> None:
        if not _tuple(self, "items"):
            raise ValueError("InList needs at least one item")


@dataclass(frozen=True)
class IsNull:
    e: Expr
    negated: bool = False


@dataclass(frozen=True)
class Between:
    e: Expr
    lo: Expr
    hi: Expr


@dataclass(frozen=True)
class KeyHash:
    """Keyhash bucket in ``[0, 1_000_000)`` of ``seed || '|' || part…`` (spec 19, B2)."""

    parts: tuple[Expr, ...]
    seed: ParamRef

    def __post_init__(self) -> None:
        if not _tuple(self, "parts"):
            raise ValueError("KeyHash needs at least one key part")
        if self.seed.param.family != "string":
            raise ValueError("the keyhash seed is bound as a string parameter")


Expr = (
    Column
    | ParamRef
    | Literal
    | Star
    | Agg
    | CountIf
    | Lower
    | Trim
    | SplitPrefix
    | Like
    | Cast
    | Coalesce
    | Case
    | Now
    | Compare
    | And
    | Or
    | Not
    | InList
    | IsNull
    | Between
    | KeyHash
)


# --- relations ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TableSample:
    """Block (``system``) or row (``bernoulli``) sampling of a FROM item (spec 19, B2)."""

    method: str
    fraction: Decimal
    seed: int = 0

    def __post_init__(self) -> None:
        if self.method not in TABLE_SAMPLE_METHODS:
            raise ValueError(f"TableSample method must be one of {TABLE_SAMPLE_METHODS}")
        if not isinstance(self.fraction, Decimal) or not Decimal(0) < self.fraction < 1:
            raise ValueError(
                f"TableSample fraction must be a Decimal in (0, 1), got {self.fraction}"
            )
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise TypeError(f"TableSample seed must be an int, got {self.seed!r}")


@dataclass(frozen=True)
class TableName:
    ref: TableRef
    alias: str | None = None
    sample: TableSample | None = None


@dataclass(frozen=True)
class SubqueryFrom:
    select: Select | UnionAll
    alias: str


@dataclass(frozen=True)
class Join:
    left: FromItem
    right: FromItem
    on: Expr | None
    kind: str = "LEFT"

    def __post_init__(self) -> None:
        if self.kind not in JOIN_KINDS:
            raise ValueError(f"unknown join kind {self.kind!r}")
        if (self.kind == "CROSS") != (self.on is None):
            raise ValueError("a CROSS join has no ON condition; every other join needs one")


@dataclass(frozen=True)
class CatalogColumns:
    """Relation ``(column_name, data_type)`` describing ``table`` (or only ``column``)."""

    table: TableRef
    column: Ident | None = None


FromItem = TableName | SubqueryFrom | Join | CatalogColumns


# --- statements --------------------------------------------------------------------------


@dataclass(frozen=True)
class Aliased:
    expr: Expr
    alias: str


@dataclass(frozen=True)
class OrderItem:
    expr: Expr
    descending: bool = False


@dataclass(frozen=True)
class Select:
    columns: tuple[Aliased, ...]
    from_: FromItem
    where: Expr | None = None
    group_by: tuple[Expr, ...] = ()
    order_by: tuple[OrderItem, ...] = ()
    limit: int | None = None

    def __post_init__(self) -> None:
        cols = _tuple(self, "columns")
        _tuple(self, "group_by")
        _tuple(self, "order_by")
        if not cols:
            raise ValueError("a Select needs at least one column")
        aliases = [c.alias for c in cols if isinstance(c, Aliased)]
        if len(aliases) != len(cols):
            raise TypeError("Select.columns must hold Aliased items")
        if len(set(aliases)) != len(aliases):
            raise ValueError(f"duplicate column alias in {aliases}")
        if self.limit is not None and (
            isinstance(self.limit, bool) or not isinstance(self.limit, int) or self.limit < 1
        ):
            raise ValueError(f"limit must be a positive int, got {self.limit!r}")

    @property
    def aliases(self) -> tuple[str, ...]:
        return tuple(c.alias for c in self.columns)


@dataclass(frozen=True)
class UnionAll:
    selects: tuple[Select, ...]

    def __post_init__(self) -> None:
        selects = _tuple(self, "selects")
        if len(selects) < 2:
            raise ValueError("UnionAll needs at least two selects")
        # explicit raises, not asserts: a caller's lowering builds this, and the check must
        # hold under python -O too (the class is the one callers always saw)
        first = selects[0]
        if not isinstance(first, Select):
            raise AssertionError(f"every branch of a UnionAll must be a Select, got {first!r}")
        for s in selects[1:]:
            if not isinstance(s, Select):
                raise AssertionError(f"every branch of a UnionAll must be a Select, got {s!r}")
            if s.aliases != first.aliases:
                raise ValueError("every branch of a UnionAll must have the same column aliases")

    @property
    def aliases(self) -> tuple[str, ...]:
        return self.selects[0].aliases


Node = Expr | FromItem | Aliased | OrderItem | Select | UnionAll | TableSample


def walk(node: object) -> Iterator[object]:
    """Yield ``node`` and every AST node below it, depth first, in field order."""
    yield node
    if not dataclasses.is_dataclass(node) or isinstance(node, (Param, TableRef, Ident)):
        return
    for f in dataclasses.fields(node):
        yield from _walk_value(getattr(node, f.name))


def _walk_value(value: object) -> Iterator[object]:
    if isinstance(value, tuple):
        for v in value:
            yield from _walk_value(v)
    elif dataclasses.is_dataclass(value) and not isinstance(value, (type, Param, TableRef, Ident)):
        yield from walk(value)


def params_of(node: object) -> tuple[Param, ...]:
    """Every distinct parameter referenced under ``node``, in first-use order.

    One name bound to two different parameters is a ``RenderError`` (FL500): binding would
    silently send one value where the other was meant.
    """
    seen: dict[str, Param] = {}
    for n in walk(node):
        if not isinstance(n, ParamRef):
            continue
        p = n.param
        prior = seen.setdefault(p.name, p)
        if prior != p:
            raise RenderError(
                f"FL500 parameter name {p.name!r} is bound to two different values "
                f"({prior.family}:{prior.value!r} and {p.family}:{p.value!r})"
            )
    return tuple(seen.values())


__all__ = [
    "AGG_FUNCTIONS",
    "COMPARE_OPS",
    "JOIN_KINDS",
    "LITERAL_CONSTANTS",
    "TABLE_SAMPLE_METHODS",
    "Agg",
    "Aliased",
    "And",
    "Between",
    "Case",
    "Cast",
    "CatalogColumns",
    "Coalesce",
    "Column",
    "Compare",
    "CountIf",
    "Expr",
    "FromItem",
    "InList",
    "IsNull",
    "Join",
    "KeyHash",
    "Like",
    "Literal",
    "Lower",
    "Node",
    "Not",
    "Now",
    "Or",
    "OrderItem",
    "ParamRef",
    "Select",
    "SplitPrefix",
    "Star",
    "SubqueryFrom",
    "TableName",
    "TableSample",
    "Trim",
    "UnionAll",
    "params_of",
    "walk",
]
