"""Helpers shared by the standard verbs (not a verb module: absent from ``_MODULES``)."""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping
from decimal import Decimal

from factlang import ir
from factlang.ir import ColumnAggregate, CompositeKeyAggregate, Metric
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.refs import Ident

ALL_OPS: tuple[str, ...] = ("==", "!=", "<", "<=", ">", ">=")

KEY_METRICS: tuple[Metric, ...] = (
    Metric.NON_NULL,
    Metric.DISTINCT_KEYS,
    Metric.MAX_PER_KEY,
    Metric.DUPLICATE_KEYS,
)


def number(value: object) -> Decimal | None:
    """Exact ``Decimal`` of a measured number; ``None`` for NULL (or a non-finite value)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value)) if math.isfinite(value) else None
    raise TypeError(f"expected a number, got {value!r}")


def ratio(numerator: object, denominator: object) -> Decimal | None:
    """``numerator / denominator`` exactly; ``None`` (never 0) when either is NULL or the
    denominator is 0."""
    n, d = number(numerator), number(denominator)
    if n is None or d is None or d == 0:
        return None
    return n / d


def null_rate(payload: Mapping[str, object]) -> Decimal | None:
    """``(row_count - non_null) / row_count``; ``None`` when there are no rows."""
    rows, non_null = number(payload["row_count"]), number(payload["non_null"])
    if rows is None or non_null is None:
        return None
    return ratio(rows - non_null, rows)


def key_plan(claim: CheckedClaim, metrics: tuple[Metric, ...]) -> ir.ProbePlan:
    """``ColumnAggregate`` on the column, or ``CompositeKeyAggregate`` when ``WITH`` is given."""
    if claim.has("WITH"):
        extra = claim.value("WITH")
        assert isinstance(extra, tuple) and all(isinstance(c, Ident) for c in extra)
        columns = (claim.column.column, *extra)
        return CompositeKeyAggregate(claim.table, columns, metrics, filter=claim.filter)
    return ColumnAggregate(claim.table, claim.column.column, metrics, filter=claim.filter)


def as_utc(value: dt.date) -> dt.datetime:
    """A timestamp in UTC: naive timestamps are UTC, a DATE is its midnight UTC (spec 6.2)."""
    if not isinstance(value, dt.datetime):
        return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc)
