"""Catalogs (spec 9.2): optional knowledge of tables and columns.

``None`` always means "unknown", never "absent"; only an authoritative catalog answers ``False``.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from factlang.semantics.refs import TableRef


class Catalog(Protocol):
    def table_exists(self, ref: TableRef) -> bool | None: ...

    def columns(self, ref: TableRef) -> dict[str, str] | None: ...


class NullCatalog:
    """Knows nothing: every answer is unknown."""

    def table_exists(self, ref: TableRef) -> bool | None:
        return None

    def columns(self, ref: TableRef) -> dict[str, str] | None:
        return None


class StaticCatalog:
    """Authoritative in-memory catalog keyed by dotted table key.

    Unquoted parts of a reference match case-insensitively; a quoted part matches exactly.
    The fullest key is tried first (``catalog.schema.table``, then ``schema.table``, then
    ``table``).
    """

    def __init__(self, tables: Mapping[str, Mapping[str, str]]) -> None:
        self._exact: dict[str, dict[str, str]] = {}
        self._lower: dict[str, dict[str, str]] = {}
        for key, cols in tables.items():
            norm = {name: str(typ).lower() for name, typ in cols.items()}
            self._exact[key] = norm
            self._lower.setdefault(key.lower(), norm)

    @classmethod
    def from_json(cls, path: str | Path) -> StaticCatalog:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(data)

    def _find(self, ref: TableRef) -> dict[str, str] | None:
        parts = ref.parts()
        for start in range(len(parts)):
            sub = parts[start:]
            key = ".".join(p.text if p.quoted else p.text.lower() for p in sub)
            hit = self._exact.get(key)
            if hit is None and not any(p.quoted for p in sub):
                hit = self._lower.get(key)
            if hit is not None:
                return hit
        return None

    def table_exists(self, ref: TableRef) -> bool | None:
        return self._find(ref) is not None

    def columns(self, ref: TableRef) -> dict[str, str] | None:
        hit = self._find(ref)
        return dict(hit) if hit is not None else None


class CachedCatalog:
    """TTL + LRU cache over another catalog; ``None`` answers are cached too."""

    def __init__(
        self,
        inner: Catalog,
        ttl_s: float = 300,
        max_tables: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._inner = inner
        self._ttl = ttl_s
        self._max = max_tables
        self._clock = clock
        self._cache: OrderedDict[tuple[str, TableRef], tuple[float, object]] = OrderedDict()

    def _get(self, kind: str, ref: TableRef, load: Callable[[TableRef], object]) -> object:
        key = (kind, ref)
        now = self._clock()
        entry = self._cache.get(key)
        if entry is not None and now - entry[0] < self._ttl:
            self._cache.move_to_end(key)
            return entry[1]
        value = load(ref)
        self._cache[key] = (now, value)
        self._cache.move_to_end(key)
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)
        return value

    def table_exists(self, ref: TableRef) -> bool | None:
        value = self._get("exists", ref, self._inner.table_exists)
        assert value is None or isinstance(value, bool)
        return value

    def columns(self, ref: TableRef) -> dict[str, str] | None:
        value = self._get("columns", ref, self._inner.columns)
        if value is None:
            return None
        assert isinstance(value, dict)
        return dict(value)
