"""Verb signatures and the verb registry (spec 6, 12)."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from enum import Enum

from factlang.semantics.types import get_type

_UPPER = re.compile(r"[A-Z][A-Z0-9_]*")


class OperandKind(Enum):
    TABLE = "TABLE"
    COLUMN = "COLUMN"
    EDGE = "EDGE"


@dataclass(frozen=True)
class OperandSpec:
    name: str
    kind: OperandKind


class Operand:
    """Shorthands for the common operand specs (spec 10): ``Operand.column()`` etc."""

    @staticmethod
    def table(name: str = "table") -> OperandSpec:
        return OperandSpec(name, OperandKind.TABLE)

    @staticmethod
    def column(name: str = "column") -> OperandSpec:
        return OperandSpec(name, OperandKind.COLUMN)

    @staticmethod
    def edge(name: str = "edge") -> OperandSpec:
        return OperandSpec(name, OperandKind.EDGE)


@dataclass(frozen=True)
class AttrSpec:
    """``ops == ()``: an operator is forbidden; otherwise one of ``ops`` is required."""

    name: str
    type: str
    required: bool = False
    ops: tuple[str, ...] = ()
    doc: str = ""


@dataclass(frozen=True)
class VerbSpec:
    """``requires`` holds ``(a, b)`` pairs: if attribute ``a`` is present, ``b`` is required.

    ``exclusive`` holds ``(a, b)`` pairs that may not both appear (FL309). ``paired`` holds
    ``(a, b)`` pairs of COLUMN_LIST attributes (edge verbs) that must appear together and
    list the same number of columns. ``cast_attr`` names the attribute whose presence
    suppresses the cross-family FL312 warning on an edge verb. ``existence_subject`` marks a
    verb whose table operand is the subject of an existence test (``EXISTS``): a catalog that
    says the table is absent is that claim's answer (FAIL), not an FL311 name error.
    """

    name: str
    summary: str
    operands: tuple[OperandSpec, ...]
    attrs: tuple[AttrSpec, ...] = ()
    filterable: bool = False
    requires: tuple[tuple[str, str], ...] = ()
    exclusive: tuple[tuple[str, str], ...] = ()
    paired: tuple[tuple[str, str], ...] = ()
    cast_attr: str | None = None
    existence_subject: bool = False

    def __post_init__(self) -> None:
        if not _UPPER.fullmatch(self.name):
            raise ValueError(f"verb name {self.name!r} must be upper-case (A-Z, 0-9, _)")
        seen: set[str] = set()
        for a in self.attrs:
            if not _UPPER.fullmatch(a.name):
                raise ValueError(
                    f"{self.name}: attribute name {a.name!r} must be upper-case (A-Z, 0-9, _)"
                )
            if a.name == "WHERE":
                raise ValueError(f"{self.name}: attribute name 'WHERE' is reserved")
            if a.name in seen:
                raise ValueError(f"{self.name}: duplicate attribute {a.name!r}")
            seen.add(a.name)
        names = [o.name for o in self.operands]
        if len(set(names)) != len(names):
            raise ValueError(f"{self.name}: duplicate operand name")
        for field, pairs in (
            ("requires", self.requires),
            ("exclusive", self.exclusive),
            ("paired", self.paired),
        ):
            for first, second in pairs:
                if field in ("exclusive", "paired") and first == second:
                    raise ValueError(f"{self.name}: {field} pairs {first!r} with itself")
                for n in (first, second):
                    if n not in seen:
                        raise ValueError(f"{self.name}: {field} names undeclared attribute {n!r}")
        types = {a.name: a.type for a in self.attrs}
        for first, second in self.paired:
            for n in (first, second):
                if types[n] != "COLUMN_LIST":
                    raise ValueError(f"{self.name}: paired attribute {n!r} must be COLUMN_LIST")
        if self.cast_attr is not None and self.cast_attr not in seen:
            raise ValueError(
                f"{self.name}: cast_attr names undeclared attribute {self.cast_attr!r}"
            )


class VerbRegistry:
    def __init__(self) -> None:
        self._verbs: dict[str, VerbSpec] = {}

    def register(self, spec: VerbSpec) -> None:
        if spec.name in self._verbs:
            raise ValueError(f"verb {spec.name} is already registered")
        for a in spec.attrs:
            try:
                get_type(a.type)
            except KeyError:
                raise ValueError(
                    f"{spec.name}: attribute {a.name} names unregistered value type {a.type!r}"
                ) from None
        self._verbs[spec.name] = spec

    def get(self, name: str) -> VerbSpec | None:
        return self._verbs.get(name)

    def names(self) -> list[str]:
        return sorted(self._verbs)

    def suggest(self, name: str) -> str | None:
        close = difflib.get_close_matches(name.upper(), self._verbs, n=1, cutoff=0.6)
        return close[0] if close else None
