"""WarehouseCatalog: a :class:`~factlang.semantics.catalog.Catalog` backed by a connector
(spec 9.2, 6.1; layer 14, core).

One ``MetadataQuery`` probe per table, rendered with the dialect's ``information_schema``
template, and only for tables a caller asks about: it never enumerates schemas. Column
types are reported as FactLang type families (``family_of``); the native names stay
available through :meth:`WarehouseCatalog.native_columns`. Wrap it in ``CachedCatalog`` for
caching across claims.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any

from factlang.connectors.base import Connector, ExecutionError
from factlang.ir import MetadataQuery
from factlang.semantics.refs import TableRef
from factlang.sql import RenderError, family_of, get_profile, renderer_for

_EXISTS, _COLUMNS = "exists", "columns"


class WarehouseCatalog:
    """Answers ``table_exists``/``columns`` from the warehouse's own catalog.

    An empty answer means the table does not exist (``False``). When the question cannot be
    asked or answered (rendering fails, the query fails with ``FL700``, rows lack
    ``column_name``/``data_type``) the answer is ``None`` (unknown), never ``False``; the error
    is kept in :attr:`errors`. ``columns`` is also ``None`` when the dialect has no native
    profile, as its type names cannot then be mapped to families.

    A **timeout** (``FL702``) is not an answer: it is raised, so a caching wrapper does not
    store a transient failure as "unknown" for its whole TTL.

    One query serves one ``table_exists`` *and* one ``columns`` call for the same ref: the
    result is memoised until the other method has used it, or for at most ``memo_s`` seconds,
    so ``table_exists`` followed by ``columns`` (as ``CachedCatalog`` asks) costs one query.
    The memo is guarded by a lock, so one instance may serve several threads.
    """

    def __init__(
        self,
        connector: Connector,
        *,
        timeout_s: float | None = None,
        memo_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.connector = connector
        self.timeout_s = timeout_s
        self.errors: list[Exception] = []
        self._memo_s = memo_s
        self._clock = clock
        # ref -> (stored at, native columns or None, methods still entitled to reuse it)
        self._memo: dict[TableRef, tuple[float, dict[str, str] | None, set[str]]] = {}
        self._lock = threading.Lock()  # guards _memo (the query itself runs unlocked)

    def _query(self, ref: TableRef) -> dict[str, str] | None:
        try:
            probe = renderer_for(self.connector.dialect)(MetadataQuery(ref))
            rows = self.connector.execute(probe, timeout_s=self.timeout_s).rows
        except ExecutionError as exc:
            if exc.code == "FL702":
                raise
            self.errors.append(exc)
            return None
        except (RenderError, ValueError) as exc:
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

    def _lookup(self, ref: TableRef, method: str) -> dict[str, str] | None:
        with self._lock:
            now = self._clock()
            entry = self._memo.get(ref)
            if entry is not None and method in entry[2] and now - entry[0] < self._memo_s:
                entry[2].discard(method)
                if not entry[2]:
                    del self._memo[ref]
                return entry[1]
        value = self._query(ref)
        other = _COLUMNS if method == _EXISTS else _EXISTS
        with self._lock:
            self._memo[ref] = (now, value, {other})
            # drop entries past their window so the memo cannot grow without bound
            for key in [k for k, e in self._memo.items() if now - e[0] >= self._memo_s]:
                del self._memo[key]
        return value

    def native_columns(self, ref: TableRef) -> dict[str, str] | None:
        """``{column name: native type}`` (empty when the table does not exist), or ``None``.

        Always queries; it does not use or fill the memo.
        """
        return self._query(ref)

    def table_exists(self, ref: TableRef) -> bool | None:
        cols = self._lookup(ref, _EXISTS)
        return None if cols is None else bool(cols)

    def columns(self, ref: TableRef) -> dict[str, str] | None:
        cols = self._lookup(ref, _COLUMNS)
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
