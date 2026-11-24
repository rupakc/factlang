"""Rendered probes and the paramstyle binding shared with fragments (spec 8.4, layer 12).

A printer produces a ``sql_template`` whose bind parameters are neutral ``{{name}}`` tokens
and the ordered ``params`` tuple. Paramstyle is a render-time input, not a profile field:
each connector calls :meth:`RenderedProbe.bind` with its driver's style.
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from factlang.ir import Arity
from factlang.semantics.filters import Param

PARAMSTYLES: tuple[str, ...] = ("qmark", "numeric", "named", "format", "pyformat", "bigquery")
_TOKEN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


class RenderError(ValueError):
    """A plan or fragment cannot be rendered (an ``FL5xx`` code leads the message)."""


def placeholder(name: str) -> str:
    """The neutral template token a printer writes for parameter ``name``."""
    return "{{" + name + "}}"


def check_params(params: tuple[Param, ...]) -> None:
    names = [p.name for p in params]
    for p in params:
        if not isinstance(p, Param):
            raise TypeError(f"params must hold Param objects, got {p!r}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", p.name):
            raise ValueError(f"parameter name {p.name!r} is not a plain identifier")
    if len(set(names)) != len(names):
        raise ValueError(f"duplicate parameter name in {names}")


def bind_template(
    template: str, params: tuple[Param, ...], paramstyle: str
) -> tuple[str, tuple[object, ...] | dict[str, object]]:
    """Replace ``{{name}}`` tokens with the driver's placeholders (PEP 249 styles + BigQuery).

    ``qmark``/``format`` give a value tuple in occurrence order (a parameter used twice is
    passed twice); ``numeric`` numbers parameters by their position in ``params``;
    ``named``/``pyformat``/``bigquery`` give a ``{name: value}`` dict. For the ``format``
    styles a literal ``%`` in the template is doubled. An unknown token is a ``RenderError``.
    """
    if paramstyle not in PARAMSTYLES:
        raise ValueError(f"unknown paramstyle {paramstyle!r}; expected one of {PARAMSTYLES}")
    by_name = {p.name: p for p in params}
    index = {p.name: i for i, p in enumerate(params, start=1)}
    out: list[str] = []
    ordered: list[object] = []
    used: dict[str, object] = {}
    pos = 0
    for m in _TOKEN.finditer(template):
        text = template[pos : m.start()]
        out.append(text.replace("%", "%%") if paramstyle in ("format", "pyformat") else text)
        name = m.group(1)
        if name not in by_name:
            raise RenderError(f"FL500 template references unknown parameter {name!r}")
        value = by_name[name].value
        if paramstyle == "qmark":
            out.append("?")
            ordered.append(value)
        elif paramstyle == "format":
            out.append("%s")
            ordered.append(value)
        elif paramstyle == "numeric":
            out.append(f":{index[name]}")
        elif paramstyle == "named":
            out.append(f":{name}")
            used[name] = value
        elif paramstyle == "pyformat":
            out.append(f"%({name})s")
            used[name] = value
        else:  # bigquery
            out.append(f"@{name}")
            used[name] = value
        pos = m.end()
    tail = template[pos:]
    out.append(tail.replace("%", "%%") if paramstyle in ("format", "pyformat") else tail)
    sql = "".join(out)
    if paramstyle in ("qmark", "format"):
        return sql, tuple(ordered)
    if paramstyle == "numeric":
        return sql, tuple(p.value for p in params)
    return sql, used


def ansi_quote(text: str) -> str:
    """ANSI string literal: single quotes, inner quotes doubled."""
    return "'" + text.replace("'", "''") + "'"


def _inline_value(p: Param, quote_string: Callable[[str], str]) -> str:
    v = p.value
    if v is None:
        return "NULL"
    if p.family == "boolean" or isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, Decimal)) and p.family in ("integer", "decimal"):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if p.family == "timestamp" and isinstance(v, dt.datetime):
        return "TIMESTAMP " + quote_string(v.isoformat())
    if p.family == "date" and isinstance(v, dt.date):
        return "DATE " + quote_string(v.isoformat())
    return quote_string(str(v))


def inline_template(
    template: str,
    params: tuple[Param, ...],
    quote_string: Callable[[str], str] | None = None,
) -> str:
    """Display-only SQL with every parameter inlined and escaped. Never executed."""
    quote = quote_string if quote_string is not None else ansi_quote
    by_name = {p.name: p for p in params}

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in by_name:
            raise RenderError(f"FL500 template references unknown parameter {name!r}")
        return _inline_value(by_name[name], quote)

    return _TOKEN.sub(sub, template)


@dataclass(frozen=True)
class RenderedProbe:
    """One executable ``SELECT`` for one plan: neutral template + typed parameters."""

    sql_template: str
    params: tuple[Param, ...]
    arity: Arity
    plan: object
    dialect: str
    tier: str

    def __post_init__(self) -> None:
        if isinstance(self.params, list):
            object.__setattr__(self, "params", tuple(self.params))
        check_params(self.params)
        if not isinstance(self.arity, Arity):
            raise TypeError(f"arity must be an Arity, got {self.arity!r}")

    def bind(self, paramstyle: str) -> tuple[str, tuple[object, ...] | dict[str, object]]:
        return bind_template(self.sql_template, self.params, paramstyle)

    def inline(self, quote_string: Callable[[str], str] | None = None) -> str:
        return inline_template(self.sql_template, self.params, quote_string)


__all__ = [
    "PARAMSTYLES",
    "RenderError",
    "RenderedProbe",
    "ansi_quote",
    "bind_template",
    "check_params",
    "inline_template",
    "placeholder",
]
