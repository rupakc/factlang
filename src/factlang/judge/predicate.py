"""The deliberately weak predicate language of ``decide`` (spec 1.1 items 8-9, 6.4).

Three-valued: a missing or ``None`` fact is undecidable (``None``), never coerced to 0.
``And`` is False if any item is False, else None if any is None; ``Or`` is the dual.

Numbers are compared as ``Decimal`` (ints and Decimals exactly; a float via
``Decimal(str(x))``). When either side is a float, equality is
``math.isclose(rel_tol=1e-9, abs_tol=1e-9)``, and the ordering operators agree with it
(``<=`` holds for close values, ``<`` does not), so ``a == b`` and ``not (a < b or a > b)``
never disagree. Non-numbers (text, dates, booleans) compare natively; comparing values of
incompatible kinds raises ``TypeError``, which the judge reports as ``ERROR`` (a contract
defect, not an absence of data).
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
    if _is_number(actual) or _is_number(target):
        # a number against a non-number (or a non-finite float): a defect, never "unequal"
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
    """Answer verbs (spec 6.4): ``fact`` equals ``claim[attr]`` within the claim's tolerance.

    ``TOLERANCE`` (a ratio) is relative: ``|actual - target| <= tol * |target|``;
    ``ABS_TOLERANCE`` is absolute: ``|actual - target| <= tol``; both inclusive. Neither
    present means exact (``==``, with ``math.isclose`` when a float is involved).
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
        if claim.has(self.tolerance_attr):
            rel = claim.number(self.tolerance_attr)
            a, t = _to_decimal(actual), _to_decimal(target)
            if a is None or t is None:
                raise TypeError(f"cannot apply a tolerance to {actual!r} and {target!r}")
            bound, label = rel * abs(t), f"{fmt(rel * 100)}%"
        elif claim.has(self.abs_attr):
            bound = claim.number(self.abs_attr)
            a, t = _to_decimal(actual), _to_decimal(target)
            if a is None or t is None:
                raise TypeError(f"cannot apply a tolerance to {actual!r} and {target!r}")
            label = fmt(bound)
        else:
            return _compare_result(self.fact, actual, "==", target)
        diff = abs(a - t)
        ok = diff <= bound
        word = "within" if ok else "not within"
        return ok, (
            f"{self.fact} {fmt(actual)} {word} {label} of {fmt(target)} "
            f"(|diff| {fmt(diff)} {'<=' if ok else '>'} {fmt(bound)})"
        )

    def reads(self) -> tuple[set[str], set[str]]:
        return {self.fact}, {self.attr, self.tolerance_attr, self.abs_attr}


def _union(items: tuple[Predicate, ...]) -> tuple[set[str], set[str]]:
    facts: set[str] = set()
    attrs: set[str] = set()
    for item in items:
        f, a = item.reads()
        facts |= f
        attrs |= a
    return facts, attrs


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
        return True, "; ".join(why for _, why in results)

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
        return False, "; ".join(why for _, why in results)

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
    """``then`` when the claim carries ``attr``, else ``otherwise`` (absent: vacuously true)."""

    attr: str
    then: Predicate
    otherwise: Predicate | None = None

    def evaluate(self, facts: Facts, claim: CheckedClaim) -> Result:
        if claim.has(self.attr):
            return self.then.evaluate(facts, claim)
        if self.otherwise is None:
            return True, f"no {self.attr} given"
        return self.otherwise.evaluate(facts, claim)

    def reads(self) -> tuple[set[str], set[str]]:
        parts = (self.then,) if self.otherwise is None else (self.then, self.otherwise)
        facts, attrs = _union(parts)
        return facts, attrs | {self.attr}


__all__ = [
    "ABS_TOL",
    "OPS",
    "REL_TOL",
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
