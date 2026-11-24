"""Verdicts and outcomes (spec 7)."""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from types import MappingProxyType

from factlang.ir import SampleSpec


class Verdict(str, Enum):
    PASS = "pass"  # measured, predicate true
    FAIL = "fail"  # measured, predicate false
    NO_DATA = "no_data"  # measured, but a fact is undecidable (e.g. 0 rows)
    ERROR = "error"  # could not measure: execution error, timeout, cost limit, FL5xx
    UNPROBEABLE = "unprobeable"  # semantic by design
    INVALID = "invalid"  # did not pass the front end (diagnostics attached)

    def __str__(self) -> str:
        """The wire value (``"pass"``), not ``"Verdict.PASS"``; ``format`` and f-strings
        agree on every supported Python version."""
        return self.value

    def __format__(self, spec: str) -> str:
        return format(self.value, spec)


@dataclass(frozen=True)
class Evidence:
    sql: str | None = None
    params: tuple[object, ...] = ()
    dialect: str | None = None
    tier: str | None = None  # verified | native | best_effort
    duration_ms: float | None = None
    bytes_processed: int | None = None
    fused_with: tuple[str, ...] = ()
    # Amendment B (B3): the sample a raw measurement ran on; claims are always exact
    sample: SampleSpec | None = None
    method: str = "exact"  # "exact" | "sampled:<method>:<fraction>:<seed>"


def sample_method(sample: SampleSpec | None) -> str:
    """``"exact"``, or ``"sampled:<method>:<fraction>:<seed>"`` for a sampled measurement."""
    if sample is None:
        return "exact"
    return f"sampled:{sample.method}:{sample.fraction}:{sample.seed}"


@dataclass(frozen=True)
class Outcome:
    claim_text: str
    canonical_text: str
    semantic_hash: str
    verb: str
    verdict: Verdict
    explanation: str
    facts: Mapping[str, object]
    evidence: Evidence | None
    diagnostics: tuple[dict[str, object], ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "facts", MappingProxyType(dict(self.facts)))
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))

    def to_dict(self) -> dict[str, object]:
        """JSON-safe dict: Decimal -> str, temporals -> ISO-8601 (datetimes in UTC),
        tuples -> lists, enums -> their value."""
        out = json_safe({f.name: getattr(self, f.name) for f in dataclasses.fields(self)})
        if not isinstance(out, dict):  # pragma: no cover - json_safe maps a dict to a dict
            raise TypeError("Outcome.to_dict produced a non-dict")
        return out


def json_safe(value: object) -> object:
    """Convert ``value`` into plain JSON types (see :meth:`Outcome.to_dict`)."""
    if isinstance(value, Enum):  # before str: Verdict is a str subclass
        return json_safe(value.value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dt.datetime):
        aware = value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value
        return aware.astimezone(dt.timezone.utc).isoformat()
    if isinstance(value, (dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, Mapping):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, set, frozenset)):
        items = [json_safe(v) for v in value]
        if isinstance(value, (set, frozenset)):
            items.sort(key=repr)
        return items
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: json_safe(getattr(value, f.name)) for f in dataclasses.fields(value)}
    return str(value)


__all__ = ["Evidence", "Outcome", "Verdict", "json_safe", "sample_method"]
