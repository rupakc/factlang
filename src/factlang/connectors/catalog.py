"""WarehouseCatalog: a :class:`~factlang.semantics.catalog.Catalog` backed by a connector
(spec 9.2, 6.1; layer 14, core).

One ``MetadataQuery`` probe per table, rendered with the dialect's ``information_schema``
template, and only for tables a caller asks about: it never enumerates schemas. Column
types are reported as FactLang type families (``family_of``); the native names stay
available through :meth:`WarehouseCatalog.native_columns`. Wrap it in ``CachedCatalog`` to
avoid asking twice.
"""

from __future__ import annotations

from typing import Any

from factlang.connectors.base import Connector, ExecutionError
from factlang.ir import MetadataQuery
from factlang.semantics.refs import TableRef
from factlang.sql import RenderError, family_of, get_profile, renderer_for


class WarehouseCatalog:
    """Answers ``table_exists``/``columns`` from the warehouse's own catalog.

    An empty answer means the table does not exist (``False``). When the question cannot be
    asked or answered (rendering fails, the query fails, rows lack ``column_name`` /
    ``data_type``) the answer is ``None`` (unknown), never ``False``; the error is kept in
    :attr:`errors`. ``columns`` is also ``None`` when the dialect has no native profile, as
    its type names cannot then be mapped to families.
    """

    def __init__(self, connector: Connector, *, timeout_s: float | None = None) -> None:
        self.connector = connector
        self.timeout_s = timeout_s
        self.errors: list[Exception] = []

    def native_columns(self, ref: TableRef) -> dict[str, str] | None:
        """``{column name: native type}`` (empty when the table does not exist), or ``None``."""
        try:
            probe = renderer_for(self.connector.dialect)(MetadataQuery(ref))
            rows = self.connector.execute(probe, timeout_s=self.timeout_s).rows
        except (ExecutionError, RenderError, ValueError) as exc:
            self.errors.append(exc)
            return None
        out: dict[str, str] = {}
        for row in rows:
            name, typ = _get(row, "column_name"), _get(row, "data_type")
            if name is None or typ is None:
                self.errors.append(
                    ExecutionError(
                        f"catalog row lacks column_name/data_type: {sorted(row)}", code="FL700"
                    )
                )
                return None
            out[str(name)] = str(typ)
        return out

    def table_exists(self, ref: TableRef) -> bool | None:
        cols = self.native_columns(ref)
        return None if cols is None else bool(cols)

    def columns(self, ref: TableRef) -> dict[str, str] | None:
        cols = self.native_columns(ref)
        if not cols:
            return None
        profile = get_profile(self.connector.dialect)
        if profile is None:
            return None
        return {name: family_of(typ, profile) for name, typ in cols.items()}


def _get(row: dict[str, Any], key: str) -> Any:
    if key in row:
        return row[key]
    for k, v in row.items():
        if isinstance(k, str) and k.lower() == key:
            return v
    return None


__all__ = ["WarehouseCatalog"]
