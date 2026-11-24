"""Dialect registry and the public fragment renderers (spec 8.3, 18 R6; layer 12).

Resolution order for ``dialect="x"`` (spec 8.3): the native profile if one is registered;
else the sqlglot bridge if it is installed; else ``FL504 unknown dialect``.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType, ModuleType
from typing import Any, Literal

from factlang.ir import ProbePlan
from factlang.semantics.filters import FilterExpr
from factlang.semantics.refs import EdgeRef, TableRef
from factlang.sql.fragment import JoinEdge, RenderedFragment, lower_filter, lower_join
from factlang.sql.printer import Printer, render
from factlang.sql.probe import RenderedProbe, RenderError
from factlang.sql.profile import DialectProfile
from factlang.sql.profiles import BUILTIN_PROFILES

# FactLang dialect name -> sqlglot dialect name (used to cross-check rendered SQL).
SQLGLOT_NAMES: Mapping[str, str] = MappingProxyType(
    {
        "bigquery": "bigquery",
        "snowflake": "snowflake",
        "redshift": "redshift",
        "postgres": "postgres",
        "duckdb": "duckdb",
        "databricks": "databricks",
        "clickhouse": "clickhouse",
        "tsql": "tsql",
        "mysql": "mysql",
        "oracle": "oracle",
        "trino": "trino",
    }
)

_PROFILES: dict[str, DialectProfile] = {}


def register_profile(profile: DialectProfile, *, replace: bool = False) -> None:
    """Register a native profile under ``profile.name``; a duplicate raises unless ``replace``."""
    if not isinstance(profile, DialectProfile):
        raise TypeError(f"expected a DialectProfile, got {profile!r}")
    if profile.name in _PROFILES and not replace:
        raise ValueError(f"dialect profile {profile.name!r} is already registered")
    _PROFILES[profile.name] = profile


def get_profile(name: str) -> DialectProfile | None:
    return _PROFILES.get(name)


def native_dialects() -> list[str]:
    """Names of every registered native profile, sorted."""
    return sorted(_PROFILES)


def _bridge() -> ModuleType | None:
    """The sqlglot bridge module, or ``None`` when sqlglot (or the bridge) is unavailable."""
    try:
        importlib.import_module("sqlglot")
        return importlib.import_module("factlang.sql.sqlglot_bridge")
    except ImportError:
        return None


def _unknown(dialect: str) -> RenderError:
    return RenderError(
        f"FL504 unknown dialect {dialect!r}; native dialects: {', '.join(native_dialects())}; "
        "for any other sqlglot dialect: pip install factlang[sqlglot]"
    )


def renderer_for(
    dialect: str, *, prefer: Literal["native", "sqlglot"] = "native"
) -> Callable[[ProbePlan], RenderedProbe]:
    """The renderer for ``dialect`` (spec 8.3 resolution order); ``FL504`` when none exists.

    ``prefer="sqlglot"`` forces the bridge (used by tests to cross-check native output).
    The bridge is expected to expose ``render(plan, dialect) -> RenderedProbe``.
    """
    if prefer not in ("native", "sqlglot"):
        raise ValueError(f"prefer must be 'native' or 'sqlglot', got {prefer!r}")
    profile = _PROFILES.get(dialect)
    if prefer == "native" and profile is not None:
        native = profile

        def render_native(plan: ProbePlan) -> RenderedProbe:
            return render(plan, native)

        return render_native
    bridge = _bridge()
    if bridge is None:
        raise _unknown(dialect)
    bridge_render: Any = bridge.render

    def render_bridged(plan: ProbePlan) -> RenderedProbe:
        result: RenderedProbe = bridge_render(plan, dialect)
        return result

    return render_bridged


def _profile(dialect: str | DialectProfile) -> DialectProfile:
    if isinstance(dialect, DialectProfile):
        return dialect
    profile = _PROFILES.get(dialect)
    if profile is None:
        raise _unknown(dialect)
    return profile


def _fragment(text: str, printer: Printer, profile: DialectProfile) -> RenderedFragment:
    if ";" in text:
        raise RenderError(f"FL500 rendered fragment failed the guard: {text[:80]!r}")
    return RenderedFragment(text, printer.params, profile.name, profile.tier)


def render_filter(
    filter: FilterExpr,
    *,
    dialect: str | DialectProfile,
    table: TableRef | None = None,
    alias: str | None = None,
    param_prefix: str = "p",
) -> RenderedFragment:
    """A resolved filter as a bindable boolean expression (R6), columns qualified by ``alias``.

    ``table`` (optional) is checked against the dialect's identifier rules and table-part
    limit (FL502/FL505). Parameters are named ``<param_prefix>1``, ….
    """
    profile = _profile(dialect)
    printer = Printer(profile)
    if table is not None:
        printer.table(table)
    text = printer.expression(lower_filter(filter, alias, param_prefix=param_prefix))
    return _fragment(text, printer, profile)


def render_join(
    edges: Sequence[EdgeRef | JoinEdge],
    dialect: str | DialectProfile,
    *,
    filters: Mapping[TableRef, FilterExpr] | None = None,
    aliases: Mapping[TableRef, str] | None = None,
    param_prefix: str = "p",
) -> RenderedFragment:
    """``FROM a JOIN b ON … [WHERE …]`` for a join path (R6), bindable, never executed here.

    The left-most table's filter is the ``WHERE``; every other table's filter is ANDed into
    the ``ON`` of the join that brings it in (see :func:`~factlang.sql.fragment.lower_join`).
    """
    profile = _profile(dialect)
    from_, where = lower_join(edges, filters, aliases, param_prefix=param_prefix)
    printer = Printer(profile)
    text = "FROM " + printer.from_clause(from_)
    if where is not None:
        text += " WHERE " + printer.expression(where)
    return _fragment(text, printer, profile)


for _p in BUILTIN_PROFILES:
    register_profile(_p)


__all__ = [
    "SQLGLOT_NAMES",
    "get_profile",
    "native_dialects",
    "register_profile",
    "render_filter",
    "render_join",
    "renderer_for",
]
