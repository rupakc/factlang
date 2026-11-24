"""The deliberately weak predicate language of ``decide`` (spec 1.1 items 8-9, 6.4).

Three-valued: a missing or ``None`` fact is undecidable (``None``), never coerced to 0.
``And`` is False if any item is False, else None if any is None; ``Or`` is the dual.

Numbers are compared as ``Decimal`` (ints and Decimals exactly; a float via
``Decimal(str(x))``). When either side is a float, equality is
``math.isclose(rel_tol=1e-9, abs_tol=1e-9)``, and the ordering operators agree with it
(``<=`` holds for close values, ``<`` does not), so ``a == b`` and ``not (a < b or a > b)``
never disagree. Non-numbers compare natively within one kind (text, boolean, date, naive
timestamp, aware timestamp, ...). Values of incompatible kinds -- a number against a
non-number, a date against a timestamp, a naive against an aware timestamp, text against
anything else -- raise ``TypeError`` for EVERY operator, ``==`` and ``!=`` included; the
judge reports that as ``ERROR`` (a contract defect, not an absence of data).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from factlang.semantics.checked import CheckedClaim
from factlang.syntax.formatter import format_number

Facts = Mapping[str, object]
Result = tuple[bool | None, str]

OPS: tuple[str, ...] = ("==", "!=", "<", "<=", ">", ">=")
_NEGATED = {"==": "!=", "!=": "==", "<": ">=", "<=": ">", ">": "<=", ">=": "<"}
REL_TOL = 1e-9
ABS_TOL = 1e-9


@runtime_checkable
class Predicate(Protocol):
    def evaluate(self, facts: Facts, claim: CheckedClaim) -> Result: ...

    def reads(self) -> tuple[set[str], set[str]]:
        """(facts read, claim attributes read): checked against the contract (FL600/601)."""
        ...


def _norm_op(op: str) -> str:
    op = "==" if op == "=" else op
    if op not in OPS:
        raise ValueError(f"unknown comparison operator {op!r}; expected one of {OPS}")
    return op


def fmt(value: object) -> str:
    """Display text of a fact or bound in an explanation."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format_number(value) if value.is_finite() else str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    return str(value)


def _to_decimal(value: object) -> Decimal | None:
    """Exact Decimal of a number; ``None`` for non-numbers and non-finite values."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value)) if math.isfinite(value) else None
    return None


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float, Decimal)) and not isinstance(value, bool)


def _kind(value: object) -> str:
    """Comparison kind: values of different kinds are never compared."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dt.datetime):
        return "timestamp" if value.tzinfo is None else "timestamp_tz"
    if isinstance(value, dt.date):
        return "date"
    if isinstance(value, dt.time):
        return "time" if value.tzinfo is None else "time_tz"
    return type(value).__name__


def compare(actual: object, op: str, target: object) -> bool:
    """``actual <op> target`` under the module's numeric rules. Raises ``TypeError``."""
    op = _norm_op(op)
    a, b = _to_decimal(actual), _to_decimal(target)
    if a is not None and b is not None:
        if isinstance(actual, float) or isinstance(target, float):
            close = math.isclose(float(a), float(b), rel_tol=REL_TOL, abs_tol=ABS_TOL)
            sign = 0 if close else (-1 if a < b else 1)
        else:
            sign = 0 if a == b else (-1 if a < b else 1)
        return {
            "==": sign == 0,
            "!=": sign != 0,
            "<": sign < 0,
            "<=": sign <= 0,
            ">": sign > 0,
            ">=": sign >= 0,
        }[op]
    if _is_number(actual) or _is_number(target) or _kind(actual) != _kind(target):
        # incompatible kinds: a defect, never "unequal" -- for every operator
        raise TypeError(f"cannot compare {actual!r} with {target!r}")
    if op == "==":
        return bool(actual == target)
    if op == "!=":
        return bool(actual != target)
    try:
        if op == "<":
            return bool(actual < target)  # type: ignore[operator]
        if op == "<=":
            return bool(actual <= target)  # type: ignore[operator]
        if op == ">":
            return bool(actual > target)  # type: ignore[operator]
        return bool(actual >= target)  # type: ignore[operator]
    except TypeError:
        raise TypeError(f"cannot compare {actual!r} with {target!r}") from None


def _missing(facts: Facts, fact: str) -> Result | None:
    if facts.get(fact) is None:
        return None, f"{fact} was not measured"
    return None


def _compare_result(fact: str, actual: object, op: str, target: object) -> Result:
    ok = compare(actual, op, target)
    shown = op if ok else _NEGATED[op]
    return ok, f"{fact} {fmt(actual)} {shown} {fmt(target)}"


@dataclass(frozen=True)
class Compare:
    """``fact <op> value`` or ``fact <op> claim[attr]`` (exactly one of value/attr)."""

    fact: str
    op: str
    value: object = None
    attr: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "op", _norm_op(self.op))
        if (self.value is None) == (self.attr is None):
            raise ValueError("Compare needs exactly one of value= or attr=")

    def evaluate(self, facts: Facts, claim: CheckedClaim) -> Result:
        missing = _missing(facts, self.fact)
        if missing is not None:
            return missing
        target = claim.value(self.attr) if self.attr is not None else self.value
        return _compare_result(self.fact, facts[self.fact], self.op, target)

    def reads(self) -> tuple[set[str], set[str]]:
        return {self.fact}, set() if self.attr is None else {self.attr}


@dataclass(frozen=True)
class CompareUsingClaimOp:
    """``fact <op> claim[attr]`` where ``op`` is the operator the author wrote (bare: ``==``)."""

    fact: str
    attr: str

    def evaluate(self, facts: Facts, claim: CheckedClaim) -> Result:
        missing = _missing(facts, self.fact)
        if missing is not None:
            return missing
        op = _norm_op(claim.op(self.attr) or "==")
        return _compare_result(self.fact, facts[self.fact], op, claim.value(self.attr))

    def reads(self) -> tuple[set[str], set[str]]:
        return {self.fact}, {self.attr}


@dataclass(frozen=True)
class Within:
    """Answer verbs (spec 6.4): ``fact <op> claim[attr]`` with the claim's tolerance band.

    ``op`` is the operator the author wrote on ``attr`` (bare: ``==``). The band is
    ``max(TOLERANCE * |target|, ABS_TOLERANCE)`` over the attributes present (0 when neither
    is: exact, ``math.isclose`` when a float is involved). With a band ``b``:
    ``==`` |a-t| <= b; ``!=`` |a-t| > b; ``>=`` a >= t-b; ``>`` a > t-b; ``<=`` a <= t+b;
    ``<`` a < t+b. Arithmetic is in ``Decimal`` (a float via ``Decimal(str(x))``).
    """

    fact: str
    attr: str
    tolerance_attr: str = "TOLERANCE"
    abs_attr: str = "ABS_TOLERANCE"

    def evaluate(self, facts: Facts, claim: CheckedClaim) -> Result:
        missing = _missing(facts, self.fact)
        if missing is not None:
            return missing
        actual, target = facts[self.fact], claim.value(self.attr)
        op = _norm_op(claim.op(self.attr) or "==")
        has_rel, has_abs = claim.has(self.tolerance_attr), claim.has(self.abs_attr)
        if not (has_rel or has_abs):
            return _compare_result(self.fact, actual, op, target)
        a, t = _to_decimal(actual), _to_decimal(target)
        if a is None or t is None:
            raise TypeError(f"cannot apply a tolerance to {actual!r} and {target!r}")
        band = Decimal(0)
        if has_rel:
            band = max(band, claim.number(self.tolerance_attr) * abs(t))
        if has_abs:
            band = max(band, claim.number(self.abs_attr))
        diff = abs(a - t)
        ok = {
            "==": diff <= band,
            "!=": diff > band,
            ">=": a >= t - band,
            ">": a > t - band,
            "<=": a <= t + band,
            "<": a < t + band,
        }[op]
        verdict = "satisfies" if ok else "violates"
        return ok, f"{self.fact} {fmt(actual)} {verdict} {op} {fmt(target)} (band {fmt(band)})"

    def reads(self) -> tuple[set[str], set[str]]:
        return {self.fact}, {self.attr, self.tolerance_attr, self.abs_attr}


ALWAYS_WHY = "always"


@dataclass(frozen=True)
class Always:
    """Always true; reads nothing (e.g. the ``otherwise`` of an optional check).

    ``And``/``Or`` leave its explanation out when joining, so an absent optional check
    does not dilute the explanation that names the numbers."""

    def evaluate(self, facts: Facts, claim: CheckedClaim) -> Result:
        return True, ALWAYS_WHY

    def reads(self) -> tuple[set[str], set[str]]:
        return set(), set()


def _union(items: tuple[Predicate, ...]) -> tuple[set[str], set[str]]:
    facts: set[str] = set()
    attrs: set[str] = set()
    for item in items:
        f, a = item.reads()
        facts |= f
        attrs |= a
    return facts, attrs


def _joined(whys: list[str]) -> str:
    """``"; "``-joined explanations without the ``Always`` parts (``"always"`` if only those)."""
    return "; ".join(w for w in whys if w != ALWAYS_WHY) or ALWAYS_WHY


def _items_init(obj: object, items: tuple[Predicate, ...]) -> None:
    if not items:
        raise ValueError(f"{type(obj).__name__} needs at least one item")
    object.__setattr__(obj, "items", tuple(items))


@dataclass(frozen=True, init=False)
class And:
    items: tuple[Predicate, ...]

    def __init__(self, *items: Predicate) -> None:
        _items_init(self, items)

    def evaluate(self, facts: Facts, claim: CheckedClaim) -> Result:
        results = [item.evaluate(facts, claim) for item in self.items]
        falses = [why for ok, why in results if ok is False]
        if falses:
            return False, falses[0]
        nones = [why for ok, why in results if ok is None]
        if nones:
            return None, "; ".join(nones)
        return True, _joined([why for _, why in results])

    def reads(self) -> tuple[set[str], set[str]]:
        return _union(self.items)


@dataclass(frozen=True, init=False)
class Or:
    items: tuple[Predicate, ...]

    def __init__(self, *items: Predicate) -> None:
        _items_init(self, items)

    def evaluate(self, facts: Facts, claim: CheckedClaim) -> Result:
        results = [item.evaluate(facts, claim) for item in self.items]
        trues = [why for ok, why in results if ok is True]
        if trues:
            return True, trues[0]
        nones = [why for ok, why in results if ok is None]
        if nones:
            return None, "; ".join(nones)
        return False, _joined([why for _, why in results])

    def reads(self) -> tuple[set[str], set[str]]:
        return _union(self.items)


@dataclass(frozen=True)
class Not:
    item: Predicate

    def evaluate(self, facts: Facts, claim: CheckedClaim) -> Result:
        ok, why = self.item.evaluate(facts, claim)
        if ok is None:
            return None, why
        return (not ok), f"not ({why})"

    def reads(self) -> tuple[set[str], set[str]]:
        return self.item.reads()


@dataclass(frozen=True)
class WhenClaimHas:
    """``then`` when the claim carries ``attr``, else ``otherwise`` (use ``Always()``)."""

    attr: str
    then: Predicate
    otherwise: Predicate

    def evaluate(self, facts: Facts, claim: CheckedClaim) -> Result:
        if claim.has(self.attr):
            return self.then.evaluate(facts, claim)
        return self.otherwise.evaluate(facts, claim)

    def reads(self) -> tuple[set[str], set[str]]:
        facts, attrs = _union((self.then, self.otherwise))
        return facts, attrs | {self.attr}


__all__ = [
    "ABS_TOL",
    "OPS",
    "REL_TOL",
    "Always",
    "And",
    "Compare",
    "CompareUsingClaimOp",
    "Facts",
    "Not",
    "Or",
    "Predicate",
    "Result",
    "WhenClaimHas",
    "Within",
    "compare",
    "fmt",
]
