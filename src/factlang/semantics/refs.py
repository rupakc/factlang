"""Resolved references (spec 4.3): identifiers and table/column/edge slots.

Equality is exact on ``(text, quoted)``; case-insensitive matching is the catalog's job.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Ident:
    text: str
    quoted: bool = False


@dataclass(frozen=True)
class TableRef:
    """``[catalog.][schema.]table``; absent leading slots are ``None``."""

    catalog: Ident | None
    schema: Ident | None
    table: Ident

    def parts(self) -> tuple[Ident, ...]:
        return tuple(p for p in (self.catalog, self.schema, self.table) if p is not None)

    def key(self) -> tuple[str | None, str | None, str]:
        return (
            self.catalog.text if self.catalog else None,
            self.schema.text if self.schema else None,
            self.table.text,
        )


@dataclass(frozen=True)
class ColumnRef:
    table: TableRef
    column: Ident


@dataclass(frozen=True)
class EdgeRef:
    left: ColumnRef
    right: ColumnRef
