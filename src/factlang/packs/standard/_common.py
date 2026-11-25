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
from factlang.semantics.signature import AttrSpec

ALL_OPS: tuple[str, ...] = ("==", "!=", "<", "<=", ">", ">=")

KEY_METRICS: tuple[Metric, ...] = (
    Metric.NON_NULL,
    Metric.DISTINCT_KEYS,
    Metric.MAX_PER_KEY,
    Metric.DUPLICATE_KEYS,
)


def number(value: object) -> Decimal | None:
    """Exact ``Decimal`` of a measured number; ``None`` for NULL (or a non-finite value).

    A string is a driver's textual NUMERIC (``Decimal(str)``); a non-numeric string raises
    ``TypeError`` (judged as ERROR)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            parsed = Decimal(value.strip())
        except ArithmeticError:
            raise TypeError(f"expected a number, got {value!r}") from None
        return parsed if parsed.is_finite() else None
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


def count(value: object) -> int | Decimal | None:
    """A measured count as ``int`` (a whole ``Decimal``/float too); ``None`` for NULL."""
    n = number(value)
    if n is None:
        return None
    return int(n) if n == n.to_integral_value() else n


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


def answer_attrs(value_type: str) -> tuple[AttrSpec, ...]:
    """``VALUE`` (all six ops, required), ``TOLERANCE`` (relative) and ``ABS_TOLERANCE``
    (absolute) of the answer-fact verbs (spec 6.4)."""
    return (
        AttrSpec("VALUE", value_type, required=True, ops=ALL_OPS, doc="the stated answer"),
        AttrSpec("TOLERANCE", "RATIO", doc="relative band around VALUE (e.g. 1%)"),
        AttrSpec("ABS_TOLERANCE", "NUMBER", doc="absolute band around VALUE"),
    )


ANSWER_EXCLUSIVE: tuple[tuple[str, str], ...] = (("TOLERANCE", "ABS_TOLERANCE"),)

#: MIN/MAX accept a temporal ``VALUE``; a band on one is judged ERROR, so it is flagged (FL314).
ANSWER_NO_TEMPORAL_BAND: tuple[str, tuple[str, ...]] = ("VALUE", ("TOLERANCE", "ABS_TOLERANCE"))


def answer_number(value: object) -> Decimal | float | None:
    """A measured SUM/AVG/MIN/MAX number: a finite float stays a float (so the judge's
    no-band ``==``/``!=`` use ``isclose``, spec 6.4); everything else is an exact ``Decimal``."""
    if isinstance(value, float) and math.isfinite(value):
        return value
    return number(value)


def answer_value(value: object, claim: CheckedClaim) -> object:
    """A measured MIN/MAX value in the kind of the claim's ``VALUE``: numbers as
    ``answer_number``; timestamps in UTC (naive = UTC); a DATE against a TIMESTAMP target is
    its midnight UTC; a timestamp against a DATE target is its UTC calendar date (the
    half-open-day semantics for all six operators). Anything else is returned unchanged
    (a kind mismatch is judged as ERROR)."""
    if value is None:
        return None
    if isinstance(value, (int, float, Decimal, str)) and not isinstance(value, bool):
        return answer_number(value)
    target = claim.value("VALUE")
    if isinstance(value, dt.datetime):
        utc = as_utc(value)
        if isinstance(target, dt.date) and not isinstance(target, dt.datetime):
            return utc.date()
        return utc
    if isinstance(value, dt.date) and isinstance(target, dt.datetime):
        return as_utc(value)
    return value
