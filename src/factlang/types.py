"""``factlang.types``: the import path of ``fl.types`` (attribute value types, spec 4.5, 6).

``from factlang.types import LiteralKind`` and ``fl.types`` name one and the same module:
importing this path puts :mod:`factlang.semantics.types` itself in its place, so a value
type registered through either path lands in the one registry.
"""

from __future__ import annotations

import sys

from factlang.semantics import types as _types
from factlang.semantics.types import (
    MAX_EXPONENT,
    SEPARATOR_CHARS,
    TYPE_FAMILIES,
    Invalid,
    Literal,
    LiteralKind,
    ValueType,
    get_type,
    register,
    register_type,
)

__all__ = [
    "MAX_EXPONENT",
    "SEPARATOR_CHARS",
    "TYPE_FAMILIES",
    "Invalid",
    "Literal",
    "LiteralKind",
    "ValueType",
    "get_type",
    "register",
    "register_type",
]

# The import system hands out whatever sys.modules holds once this body has run, and binds
# the parent package's ``types`` attribute to it, so ``fl.types`` stays the real module.
sys.modules[__name__] = _types
