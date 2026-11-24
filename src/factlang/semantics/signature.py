"""Verb signatures and the verb registry (spec 6, 12)."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from enum import Enum

_UPPER = re.compile(r"[A-Z][A-Z0-9_]*")


class OperandKind(Enum):
    TABLE = "TABLE"
    COLUMN = "COLUMN"
    EDGE = "EDGE"


@dataclass(frozen=True)
class OperandSpec:
    name: str
    kind: OperandKind


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
    """``requires`` holds ``(a, b)`` pairs: if attribute ``a`` is present, ``b`` is required."""

    name: str
    summary: str
    operands: tuple[OperandSpec, ...]
    attrs: tuple[AttrSpec, ...] = ()
    filterable: bool = False
    requires: tuple[tuple[str, str], ...] = ()

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
        for first, second in self.requires:
            for n in (first, second):
                if n not in seen:
                    raise ValueError(f"{self.name}: requires names undeclared attribute {n!r}")


class VerbRegistry:
    def __init__(self) -> None:
        self._verbs: dict[str, VerbSpec] = {}

    def register(self, spec: VerbSpec) -> None:
        if spec.name in self._verbs:
            raise ValueError(f"verb {spec.name} is already registered")
        self._verbs[spec.name] = spec

    def get(self, name: str) -> VerbSpec | None:
        return self._verbs.get(name)

    def names(self) -> list[str]:
        return sorted(self._verbs)

    def suggest(self, name: str) -> str | None:
        close = difflib.get_close_matches(name.upper(), self._verbs, n=1, cutoff=0.6)
        return close[0] if close else None
