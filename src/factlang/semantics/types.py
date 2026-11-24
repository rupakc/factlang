"""Attribute value types and their registry (spec 4.5, 6).

``coerce`` turns an AST literal into a Python value and never raises: a literal that cannot
be coerced yields an :class:`Invalid`, which ``validate`` reports as its message (the
analyser maps messages to FL309). ``validate`` receives the coerced value. Whether a literal
kind is acceptable at all is decided from ``accepts`` (FL305) before either runs.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from factlang.semantics.refs import Ident
from factlang.syntax.ast import Literal, LiteralKind
from factlang.syntax.formatter import format_number

LK = LiteralKind

__all__ = [
    "MAX_EXPONENT",
    "SEPARATOR_CHARS",
    "TYPE_FAMILIES",
    "Invalid",
    "Literal",
    "LiteralKind",
    "ValueType",
    "get_type",
    "register",
    "register_type",
]

TYPE_FAMILIES: tuple[str, ...] = (
    "integer",
    "decimal",
    "float",
    "string",
    "boolean",
    "date",
    "timestamp",
    "time",
    "bytes",
    "json",
    "array",
    "struct",
    "other",
)
SEPARATOR_CHARS = frozenset(":;|/-=#>~,+( ")


@dataclass(frozen=True)
class Invalid:
    """Result of a coercion that failed; carries the message ``validate`` returns."""

    message: str


@dataclass(frozen=True)
class ValueType:
    name: str
    accepts: frozenset[LiteralKind]
    coerce: Callable[[Literal], object] | None = None
    validate: Callable[[object], str | None] | None = None


_REGISTRY: dict[str, ValueType] = {}


def register_type(vt: ValueType, *, replace: bool = False) -> None:
    if vt.name in _REGISTRY and not replace:
        raise ValueError(f"value type {vt.name} is already registered")
    _REGISTRY[vt.name] = vt


register = register_type  # ``fl.types.register(ValueType(...))`` (spec 10)


def get_type(name: str) -> ValueType:
    try:
        return _REGISTRY[name]
    except KeyError:
        raise KeyError(f"unknown value type {name!r}") from None


def _msg(value: object) -> str | None:
    return value.message if isinstance(value, Invalid) else None


# --- numbers ------------------------------------------------------------------------


MAX_EXPONENT = 1000


def _bounded(value: Decimal) -> Decimal | Invalid:
    return Invalid("number out of range") if abs(value.adjusted()) > MAX_EXPONENT else value


def _decimal(lit: Literal) -> object:
    assert isinstance(lit.value, Decimal)
    return _bounded(lit.value)


def _integer_coerce(lit: Literal) -> object:
    v = _decimal(lit)
    if not isinstance(v, Decimal):
        return v
    return int(v) if v == v.to_integral_value() else v


def _integer_validate(value: object) -> str | None:
    if isinstance(value, Invalid):
        return value.message
    return None if isinstance(value, int) else "expected a whole number"


def _ratio_validate(value: object) -> str | None:
    if isinstance(value, Invalid):
        return value.message
    assert isinstance(value, Decimal)
    if 0 <= value <= 1:
        return None
    return f"ratio must be between 0 and 1 (0% to 100%), got {format_number(value)}"


# --- temporal -----------------------------------------------------------------------

_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}", re.ASCII)
_TS_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2})(:\d{2})?(?:\.(\d{1,6}))?)?(Z|[+-]\d{2}:\d{2})?",
    re.ASCII,
)


def _parse_date(text: str) -> dt.date | Invalid:
    if _DATE_RE.fullmatch(text):
        try:
            return dt.date.fromisoformat(text)
        except ValueError:
            pass
    return Invalid(f"invalid ISO-8601 date {text!r} (expected YYYY-MM-DD)")


def _parse_timestamp(text: str) -> dt.datetime | Invalid:
    err = Invalid(f"invalid ISO-8601 timestamp {text!r}")
    m = _TS_RE.fullmatch(text)
    if not m:
        return err
    day, hm, sec, frac, zone = m.groups()
    iso = f"{day}T{hm or '00:00'}{sec or ':00'}"
    if frac:
        iso += "." + frac.ljust(6, "0")
    iso += "+00:00" if zone in (None, "Z") else zone
    try:
        return dt.datetime.fromisoformat(iso).astimezone(dt.timezone.utc)
    except (ValueError, OverflowError):
        return err


def _temporal_coerce(lit: Literal) -> object:
    text = str(lit.value)
    return _parse_date(text) if lit.kind is LK.DATE else _parse_timestamp(text)


def _number_or_temporal_coerce(lit: Literal) -> object:
    return _decimal(lit) if lit.kind is LK.NUMBER else _temporal_coerce(lit)


# --- symbols ------------------------------------------------------------------------


def _text(lit: Literal) -> object:
    return str(lit.value)


def _family_validate(value: object) -> str | None:
    if value in TYPE_FAMILIES:
        return None
    return f"unknown type family {value!r}; expected one of: {', '.join(TYPE_FAMILIES)}"


def _separator_validate(value: object) -> str | None:
    assert isinstance(value, str)
    if not 1 <= len(value) <= 3:
        return "separator must be 1 to 3 characters"
    bad = sorted(set(value) - SEPARATOR_CHARS)
    if bad:
        return f"separator may only use : ; | / - = # > ~ , + ( and space, not {''.join(bad)!r}"
    return None


def _like_pattern_validate(value: object) -> str | None:
    assert isinstance(value, str)
    if not value.strip("%_"):
        return "LIKE pattern has only wildcards (% and _); it would match every value"
    return None


# --- lists --------------------------------------------------------------------------


def _items(lit: Literal) -> tuple[Literal, ...]:
    assert isinstance(lit.value, tuple)
    return lit.value


def _column_list_coerce(lit: Literal) -> object:
    out: list[Ident] = []
    for item in _items(lit):
        if item.kind not in (LK.IDENT, LK.QIDENT):
            return Invalid("column list may contain only column names")
        out.append(Ident(str(item.value), item.kind is LK.QIDENT))
    return tuple(out)


def _column_list_validate(value: object) -> str | None:
    if isinstance(value, Invalid):
        return value.message
    return None if value else "column list must not be empty"


def _value_text(item: Literal) -> str | None:
    if item.kind is LK.NUMBER:
        assert isinstance(item.value, Decimal)
        return None if abs(item.value.adjusted()) > MAX_EXPONENT else format_number(item.value)
    if item.kind is LK.BOOL:
        return "true" if item.value else "false"
    if item.kind is LK.STRING:
        return str(item.value)
    return None


def _value_list_coerce(lit: Literal) -> object:
    out: list[str] = []
    for item in _items(lit):
        text = _value_text(item)
        if text is None:
            return Invalid("value list may contain only numbers, strings and booleans")
        out.append(text)
    return tuple(out)


def _range_pair_coerce(lit: Literal) -> object:
    items = _items(lit)
    if len(items) != 2:
        return Invalid(f"range needs exactly 2 values, got {len(items)}")
    out: list[object] = []
    for item in items:
        if item.kind not in _NUMBER_OR_TEMPORAL:
            return Invalid("range bounds must be numbers, dates or timestamps")
        v = _number_or_temporal_coerce(item)
        if isinstance(v, Invalid):
            return v
        out.append(v)
    lo, hi = out
    kinds = {
        "number"
        if isinstance(v, Decimal)
        else "timestamp"
        if isinstance(v, dt.datetime)
        else "date"
        for v in out
    }
    if len(kinds) != 1:
        return Invalid("range bounds must both be numbers, both dates, or both timestamps")
    if lo > hi:  # type: ignore[operator]
        return Invalid("range is inverted: the low bound is greater than the high bound")
    return (lo, hi)


def _invalid_only(value: object) -> str | None:
    return _msg(value)


_NUMBER_OR_TEMPORAL = frozenset({LK.NUMBER, LK.DATE, LK.TIMESTAMP})

for _vt in (
    ValueType("NUMBER", frozenset({LK.NUMBER}), _decimal, _invalid_only),
    ValueType("INTEGER", frozenset({LK.NUMBER}), _integer_coerce, _integer_validate),
    ValueType("RATIO", frozenset({LK.PERCENT, LK.NUMBER}), _decimal, _ratio_validate),
    ValueType("STRING", frozenset({LK.STRING}), _text),
    ValueType("SYMBOL", frozenset({LK.IDENT, LK.STRING}), _text),
    ValueType("CARDINALITY", frozenset({LK.CARDINALITY}), _text),
    ValueType("DURATION", frozenset({LK.DURATION})),
    ValueType("TEMPORAL", frozenset({LK.DATE, LK.TIMESTAMP}), _temporal_coerce, _invalid_only),
    ValueType("NUMBER_OR_TEMPORAL", _NUMBER_OR_TEMPORAL, _number_or_temporal_coerce, _invalid_only),
    ValueType("TYPE_FAMILY", frozenset({LK.IDENT, LK.STRING}), _text, _family_validate),
    ValueType("COLUMN_LIST", frozenset({LK.LIST}), _column_list_coerce, _column_list_validate),
    ValueType("VALUE_LIST", frozenset({LK.LIST}), _value_list_coerce, _invalid_only),
    ValueType("RANGE_PAIR", frozenset({LK.LIST}), _range_pair_coerce, _invalid_only),
    ValueType("SEPARATOR", frozenset({LK.STRING}), _text, _separator_validate),
    ValueType("LIKE_PATTERN", frozenset({LK.STRING}), _text, _like_pattern_validate),
):
    register_type(_vt)
