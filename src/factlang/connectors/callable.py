"""CallableConnector: for hosts that run SQL themselves (spec 9.1; layer 14, core)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from factlang.connectors.base import ExecutionError, QueryResult, check_paramstyle, map_error
from factlang.sql import RenderedProbe

HostFn = Callable[[str, "tuple[object, ...] | dict[str, object]"], "list[dict[str, Any]]"]


class CallableConnector:
    """Wraps ``fn(sql, params) -> rows``; ``params`` is a tuple or dict per ``paramstyle``.

    The host function cannot be given the timeout, so it is the host's to enforce; a
    ``TimeoutError`` (or any timeout-shaped error) it raises becomes ``FL702``, any other
    exception ``FL700``. An ``ExecutionError`` it raises passes through unchanged.
    """

    def __init__(self, fn: HostFn, dialect: str, paramstyle: str = "qmark") -> None:
        self.fn = fn
        self.dialect = dialect
        self.paramstyle = check_paramstyle(paramstyle)

    def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
        sql, params = probe.bind(self.paramstyle)
        try:
            rows = self.fn(sql, params)
        except ExecutionError:
            raise
        except Exception as exc:
            raise map_error(exc, timeout_s=timeout_s) from exc
        if not isinstance(rows, list) or not all(isinstance(r, Mapping) for r in rows):
            raise map_error(
                TypeError(f"host function must return a list of mappings, got {rows!r:.80}")
            )
        return QueryResult([dict(r) for r in rows])


__all__ = ["CallableConnector", "HostFn"]
