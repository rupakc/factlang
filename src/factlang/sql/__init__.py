"""Dialect-free SQL layer: internal AST, lowering, native printer, profiles (layer 12)."""

from __future__ import annotations

from factlang.semantics.filters import Param
from factlang.sql import ast
from factlang.sql.fragment import JoinEdge, RenderedFragment, lower_filter, lower_join
from factlang.sql.lower import lower
from factlang.sql.printer import print_select, render
from factlang.sql.probe import PARAMSTYLES, RenderedProbe, RenderError
from factlang.sql.profile import DialectProfile, family_of
from factlang.sql.registry import (
    SQLGLOT_NAMES,
    get_profile,
    native_dialects,
    register_profile,
    render_filter,
    render_join,
    renderer_for,
)

__all__ = [
    "PARAMSTYLES",
    "SQLGLOT_NAMES",
    "DialectProfile",
    "JoinEdge",
    "Param",
    "RenderError",
    "RenderedFragment",
    "RenderedProbe",
    "ast",
    "family_of",
    "get_profile",
    "lower",
    "lower_filter",
    "lower_join",
    "native_dialects",
    "print_select",
    "register_profile",
    "render",
    "render_filter",
    "render_join",
    "renderer_for",
]
