"""Dialect-free measurement primitives (spec 8.1, Amendment A R2-R4, Amendment B B1).

Every primitive is a frozen, hashable dataclass with a class attribute ``arity`` and a
``result_columns()`` method naming its wire-format result columns. Validation happens in
``__post_init__`` and raises ``ValueError`` (bad value) or ``TypeError`` (bad kind). Lists
passed where a tuple is expected are normalised to tuples, so plans stay hashable.

No primitive samples its input unless its ``sample`` field is set (Amendment B). Only
``Checker.measure`` accepts such a plan; contracts and claims never produce one
(:func:`is_sampled` is the check later stages use to report FL404).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import ClassVar, Literal

from factlang.ir.metrics import ALIASES, KEY_METRICS, TABLE_METRICS, Arity, Metric
from factlang.semantics.filters import (
    FAnd,
    FBetween,
    FBool,
    FCompare,
    FilterExpr,
    FIn,
    FIsNull,
    FNot,
    FOr,
    canonical,
)
from factlang.semantics.identity import operand_json
from factlang.semantics.refs import ColumnRef, Ident, TableRef
from factlang.semantics.types import TYPE_FAMILIES, get_type
from factlang.syntax.formatter import format_number

SampleMethod = Literal["keyhash", "system", "bernoulli"]
SAMPLE_METHODS: tuple[str, ...] = ("keyhash", "system", "bernoulli")
KEYHASH_BUCKETS = 1_000_000
REPORT_LIMIT_MAX = 1000
_FILTER_TYPES = (FCompare, FBool, FIn, FIsNull, FBetween, FAnd, FOr, FNot)


def _set(obj: object, name: str, value: object) -> None:
    object.__setattr__(obj, name, value)


def _as_tuple(obj: object, name: str) -> tuple[object, ...]:
    value = getattr(obj, name)
    if isinstance(value, list):
        value = tuple(value)
        _set(obj, name, value)
    if not isinstance(value, tuple):
        raise TypeError(f"{type(obj).__name__}.{name} must be a tuple, got {value!r}")
    return value


def _check_int(owner: str, name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{owner}.{name} must be an int, got {value!r}")
    return value


def _own_column(obj: ColumnAggregate | ValueMembership | PatternCoverage | MetadataQuery) -> None:
    """Accept a ``ColumnRef`` on ``obj.table`` for the ``column`` field, store its ``Ident``."""
    table = obj.table
    column: object = obj.column
    if isinstance(column, ColumnRef):
        if column.table != table:
            raise ValueError(
                f"column {column.column.text!r} belongs to table {column.table.table.text!r}, "
                f"not {table.table.text!r}"
            )
        _set(obj, "column", column.column)
    elif column is not None and not isinstance(column, Ident):
        raise TypeError(f"{type(obj).__name__}.column must be an Ident, got {column!r}")


def _check_common(obj: object, *, tables: tuple[str, ...] = ("table",)) -> None:
    """``table`` is a TableRef; every filter is a FilterExpr or None; every sample a SampleSpec."""
    owner = type(obj).__name__
    for name in tables:
        if not isinstance(getattr(obj, name), TableRef):
            raise TypeError(f"{owner}.{name} must be a TableRef, got {getattr(obj, name)!r}")
    for name in ("filter", "right_filter"):
        value = getattr(obj, name, None)
        if value is not None and not isinstance(value, _FILTER_TYPES):
            raise TypeError(f"{owner}.{name} must be a FilterExpr or None, got {value!r}")
    for name in ("sample", "right_sample"):
        value = getattr(obj, name, None)
        if value is not None and not isinstance(value, SampleSpec):
            raise TypeError(f"{owner}.{name} must be a SampleSpec or None, got {value!r}")


def _metrics(obj: object) -> tuple[Metric, ...]:
    owner = type(obj).__name__
    metrics = _as_tuple(obj, "metrics")
    if not metrics:
        raise ValueError(f"{owner} needs at least one metric")
    for m in metrics:
        if not isinstance(m, Metric):
            raise TypeError(f"{owner}.metrics must hold Metric members, got {m!r}")
    if len(set(metrics)) != len(metrics):
        raise ValueError(f"{owner}.metrics has a duplicate metric")
    return metrics  # type: ignore[return-value]


def _sampled(cols: tuple[str, ...], sample: SampleSpec | None, name: str) -> tuple[str, ...]:
    return (*cols, name) if sample is not None else cols


@dataclass(frozen=True)
class SampleSpec:
    """How a raw measurement samples its input (Amendment B, B1). Never used by verdicts."""

    method: SampleMethod
    fraction: Decimal
    seed: int = 0
    key: tuple[Ident, ...] = ()

    def __post_init__(self) -> None:
        if self.method not in SAMPLE_METHODS:
            raise ValueError(
                f"unknown sample method {self.method!r}; expected one of "
                + ", ".join(SAMPLE_METHODS)
            )
        fraction: object = self.fraction
        if isinstance(fraction, int) and not isinstance(fraction, bool):
            fraction = Decimal(fraction)
            _set(self, "fraction", fraction)
        if not isinstance(fraction, Decimal):
            raise TypeError(f"sample fraction must be a Decimal, got {fraction!r}")
        if not fraction.is_finite() or not Decimal(0) < fraction <= Decimal(1):
            raise ValueError(f"sample fraction must be in (0, 1], got {fraction}")
        # keyhash keeps a row iff bucket < round_half_up(fraction * 1e6) (spec 19); every
        # method shares the floor so a sample can never be empty by construction
        if (fraction * KEYHASH_BUCKETS).quantize(Decimal(1), rounding=ROUND_HALF_UP) == 0:
            raise ValueError(
                f"sample fraction {fraction} is below 5e-7: it rounds to 0 of "
                f"{KEYHASH_BUCKETS} keyhash buckets and would keep no rows"
            )
        _check_int("SampleSpec", "seed", self.seed)
        key = _as_tuple(self, "key")
        for k in key:
            if not isinstance(k, Ident):
                raise TypeError(f"sample key must hold Idents, got {k!r}")
        if len(set(key)) != len(key):
            raise ValueError("sample key has a duplicate column")
        if self.method == "keyhash" and not key:
            raise ValueError("keyhash sampling needs a key (the columns hashed)")
        if self.method != "keyhash" and key:
            raise ValueError(f"{self.method} sampling takes no key")


@dataclass(frozen=True)
class ColumnAggregate:
    arity: ClassVar[Arity] = Arity.SINGLE_ROW
    table: TableRef
    column: Ident | None
    metrics: tuple[Metric, ...]
    filter: FilterExpr | None = None
    sample: SampleSpec | None = None

    def __post_init__(self) -> None:
        _check_common(self)
        _own_column(self)
        metrics = _metrics(self)
        if self.column is None:
            needs = [m.name for m in metrics if m not in TABLE_METRICS]
            if needs:
                raise ValueError(f"metric(s) {', '.join(needs)} need a column")

    def result_columns(self) -> tuple[str, ...]:
        return _sampled(tuple(ALIASES[m] for m in self.metrics), self.sample, "sampled_rows")


@dataclass(frozen=True)
class CompositeKeyAggregate:
    arity: ClassVar[Arity] = Arity.SINGLE_ROW
    table: TableRef
    columns: tuple[Ident, ...]
    metrics: tuple[Metric, ...]
    filter: FilterExpr | None = None
    sample: SampleSpec | None = None

    def __post_init__(self) -> None:
        _check_common(self)
        columns = _as_tuple(self, "columns")
        if not columns:
            raise ValueError("CompositeKeyAggregate needs at least one key column")
        for c in columns:
            if not isinstance(c, Ident):
                raise TypeError(f"CompositeKeyAggregate.columns must hold Idents, got {c!r}")
        if len(set(columns)) != len(columns):
            raise ValueError("CompositeKeyAggregate.columns has a duplicate column")
        bad = [m.name for m in _metrics(self) if m not in KEY_METRICS]
        if bad:
            raise ValueError(f"metric(s) {', '.join(bad)} are not defined over a key tuple")

    def result_columns(self) -> tuple[str, ...]:
        return _sampled(tuple(ALIASES[m] for m in self.metrics), self.sample, "sampled_rows")


@dataclass(frozen=True)
class ValueMembership:
    arity: ClassVar[Arity] = Arity.ROW_SET
    table: TableRef
    column: Ident
    declared: tuple[str, ...]
    split: str | None = None
    report_limit: int = 20
    filter: FilterExpr | None = None
    sample: SampleSpec | None = None

    def __post_init__(self) -> None:
        _check_common(self)
        _own_column(self)
        if self.column is None:
            raise TypeError("ValueMembership needs a column")
        for v in _as_tuple(self, "declared"):  # may be empty (Amendment A R2)
            if not isinstance(v, str):
                raise TypeError(f"declared values must be text, got {v!r}")
        limit = _check_int("ValueMembership", "report_limit", self.report_limit)
        if not 1 <= limit <= REPORT_LIMIT_MAX:
            raise ValueError(f"report_limit must be 1..{REPORT_LIMIT_MAX}, got {limit}")
        if self.split is not None:
            if not isinstance(self.split, str):
                raise TypeError(f"split must be a string, got {self.split!r}")
            validate = get_type("SEPARATOR").validate
            problem = validate(self.split) if validate is not None else None
            if problem is not None:
                raise ValueError(problem)

    def result_columns(self) -> tuple[str, ...]:
        summary = ("kind", "non_null", "declared_rows", "undeclared_distinct")
        return (*_sampled(summary, self.sample, "sampled_rows"), "value", "freq")


def _key_side(obj: object, name: str) -> tuple[ColumnRef, ...]:
    value = getattr(obj, name)
    if isinstance(value, ColumnRef):
        value = (value,)
        _set(obj, name, value)
    side = _as_tuple(obj, name)
    if not side:
        raise ValueError(f"KeyOverlap.{name} must not be empty")
    refs: list[ColumnRef] = []
    for c in side:
        if not isinstance(c, ColumnRef):
            raise TypeError(f"KeyOverlap.{name} must hold ColumnRefs, got {c!r}")
        refs.append(c)
    if len({c.table for c in refs}) != 1:
        raise ValueError(f"KeyOverlap.{name} columns must all be on one table")
    if len(set(refs)) != len(refs):
        raise ValueError(f"KeyOverlap.{name} has a duplicate column")
    return tuple(refs)


@dataclass(frozen=True)
class KeyOverlap:
    """Join key overlap. ``filter``/``sample`` apply to the left side, ``right_*`` to the right."""

    arity: ClassVar[Arity] = Arity.SINGLE_ROW
    left: tuple[ColumnRef, ...]
    right: tuple[ColumnRef, ...]
    cast_to: str | None = None
    measure_multiplicity: bool = False
    filter: FilterExpr | None = None
    right_filter: FilterExpr | None = None
    sample: SampleSpec | None = None
    right_sample: SampleSpec | None = None

    def __post_init__(self) -> None:
        left, right = _key_side(self, "left"), _key_side(self, "right")
        _check_common(self, tables=())
        if len(left) != len(right):
            raise ValueError(
                f"KeyOverlap key length mismatch: {len(left)} left vs {len(right)} right"
            )
        if self.cast_to is not None and self.cast_to not in TYPE_FAMILIES:
            raise ValueError(
                f"unknown cast type family {self.cast_to!r}; expected one of "
                + ", ".join(TYPE_FAMILIES)
            )
        if not isinstance(self.measure_multiplicity, bool):
            raise TypeError("KeyOverlap.measure_multiplicity must be a bool")

    @property
    def left_table(self) -> TableRef:
        return self.left[0].table

    @property
    def right_table(self) -> TableRef:
        return self.right[0].table

    def result_columns(self) -> tuple[str, ...]:
        cols: tuple[str, ...] = (
            "distinct_left",
            "matched",
            "distinct_right",
            "left_non_null",
            "matched_left_rows",
        )
        if self.measure_multiplicity:
            cols += ("max_left_per_key", "max_right_per_key")
        cols = _sampled(cols, self.sample, "sampled_left_rows")
        return _sampled(cols, self.right_sample, "sampled_right_rows")


@dataclass(frozen=True)
class PatternCoverage:
    """Case-folded ``LIKE`` coverage over non-NULL values."""

    arity: ClassVar[Arity] = Arity.SINGLE_ROW
    table: TableRef
    column: Ident
    pattern: str
    filter: FilterExpr | None = None
    sample: SampleSpec | None = None

    def __post_init__(self) -> None:
        _check_common(self)
        _own_column(self)
        if self.column is None:
            raise TypeError("PatternCoverage needs a column")
        if not isinstance(self.pattern, str):
            raise TypeError(f"pattern must be a string, got {self.pattern!r}")
        if not self.pattern.strip("%_"):
            raise ValueError(
                f"pattern {self.pattern!r} has only wildcards; it would match every value"
            )

    def result_columns(self) -> tuple[str, ...]:
        return _sampled(("non_null", "matching"), self.sample, "sampled_rows")


@dataclass(frozen=True)
class MetadataQuery:
    """Catalog metadata of a table (or one column). Never sampled."""

    arity: ClassVar[Arity] = Arity.ROW_SET
    table: TableRef
    column: Ident | None = None

    def __post_init__(self) -> None:
        _check_common(self)
        _own_column(self)

    def result_columns(self) -> tuple[str, ...]:
        return ("column_name", "data_type")


ProbePlan = (
    ColumnAggregate
    | CompositeKeyAggregate
    | ValueMembership
    | KeyOverlap
    | PatternCoverage
    | MetadataQuery
)
CORE_PRIMITIVES: tuple[type, ...] = (
    ColumnAggregate,
    CompositeKeyAggregate,
    ValueMembership,
    KeyOverlap,
    PatternCoverage,
    MetadataQuery,
)

# primitive class -> builder into the internal SQL AST (consumed by the lowering, Task 13)
PRIMITIVE_LOWERINGS: dict[type, Callable[..., object]] = {}


def register_primitive(cls: type, *, lower: Callable[..., object], replace: bool = False) -> None:
    """Register a primitive class and its lowering. Raises on a duplicate unless ``replace``."""
    if not isinstance(cls, type) or not dataclasses.is_dataclass(cls):
        raise TypeError(f"a primitive must be a dataclass class, got {cls!r}")
    params = getattr(cls, "__dataclass_params__", None)
    if params is None or not params.frozen:
        raise TypeError(f"primitive {cls.__name__} must be a frozen dataclass")
    if not isinstance(getattr(cls, "arity", None), Arity):
        raise TypeError(f"primitive {cls.__name__} must declare a class attribute arity: Arity")
    if not callable(getattr(cls, "result_columns", None)):
        raise TypeError(f"primitive {cls.__name__} must define result_columns()")
    if not callable(lower):
        raise TypeError("lower must be callable")
    if cls in PRIMITIVE_LOWERINGS and not replace:
        raise ValueError(f"primitive {cls.__name__} is already registered")
    PRIMITIVE_LOWERINGS[cls] = lower


def is_sampled(plan: object) -> bool:
    """True when ``plan`` samples any input (a claim or contract plan must not: FL404)."""
    return getattr(plan, "sample", None) is not None or (
        getattr(plan, "right_sample", None) is not None
    )


def _encode(value: object) -> object:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, Decimal):
        return {"decimal": format_number(value) if value.is_finite() else str(value)}
    if isinstance(value, float):
        return {"float": repr(value)}
    if isinstance(value, Enum):
        return {"enum": f"{type(value).__name__}.{value.name}"}
    if isinstance(value, Ident):
        return [value.text, value.quoted]
    if isinstance(value, (TableRef, ColumnRef)):
        return operand_json(value)
    if isinstance(value, _FILTER_TYPES):
        return {"filter": canonical(value)}
    if isinstance(value, dt.datetime):
        aware = value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value
        return {"timestamp": aware.astimezone(dt.timezone.utc).isoformat()}
    if isinstance(value, dt.date):
        return {"date": value.isoformat()}
    if isinstance(value, (tuple, list)):
        return [_encode(v) for v in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        cls = type(value)
        name = cls.__name__ if cls in CORE_PRIMITIVES else f"{cls.__module__}.{cls.__qualname__}"
        out: dict[str, object] = {"_type": name}
        for f in dataclasses.fields(value):
            out[f.name] = _encode(getattr(value, f.name))
        return out
    raise TypeError(f"cannot encode {value!r} in a plan key")


def canonical_key(plan: object) -> str:
    """Deterministic JSON text of ``plan``: equal keys measure the same thing (R1 de-dup).

    Filters are keyed by their canonical text (parameter names do not matter), numbers by
    their canonical spelling, and the sample spec is part of the key.
    """
    return json.dumps(_encode(plan), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


__all__ = [
    "CORE_PRIMITIVES",
    "PRIMITIVE_LOWERINGS",
    "REPORT_LIMIT_MAX",
    "SAMPLE_METHODS",
    "ColumnAggregate",
    "CompositeKeyAggregate",
    "KeyOverlap",
    "MetadataQuery",
    "PatternCoverage",
    "ProbePlan",
    "SampleMethod",
    "SampleSpec",
    "ValueMembership",
    "canonical_key",
    "is_sampled",
    "register_primitive",
]
