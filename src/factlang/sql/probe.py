"""Rendered probes and the paramstyle binding shared with fragments (spec 8.4, layer 12).

A printer produces a ``sql_template`` whose bind parameters are neutral ``{{name}}`` tokens
and the ordered ``params`` tuple. Paramstyle is a render-time input, not a profile field:
each connector calls :meth:`RenderedProbe.bind` with its driver's style.
"""

from __future__ import annotations

import datetime as dt
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from factlang.ir import Arity, ProbePlan
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


def check_template(template: str, params: tuple[Param, ...]) -> None:
    """Every ``{{name}}`` token has a parameter and every parameter is used."""
    if not isinstance(template, str):
        raise TypeError(f"sql_template must be a string, got {template!r}")
    tokens = {m.group(1) for m in _TOKEN.finditer(template)}
    names = {p.name for p in params}
    missing = sorted(tokens - names)
    if missing:
        raise RenderError(f"FL500 template references unknown parameter(s) {missing}")
    unused = sorted(names - tokens)
    if unused:
        raise RenderError(f"FL500 parameter(s) {unused} not used by the template")


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


# Python's default int <-> str conversion limit; also the bound for a Decimal's plain form, so
# a value like Decimal("1E+100000000") is refused instead of rendering 100M digits. The inline
# caps below are tighter; this stays the cheap first check.
MAX_LITERAL_DIGITS = 4300
# Inline precision caps (spec 0.1.3 I3): past these, engines silently change a literal's type
# or precision (DuckDB reads a 39-digit fraction as DOUBLE, a 39-digit integer as BIGNUM and
# a 20-digit one as HUGEINT), so the literal would not mean the value. Bound parameters are
# not affected.
MAX_DECIMAL_PRECISION = 38
INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
MAX_FLOAT_SIGNIFICANT_DIGITS = 17


@dataclass(frozen=True)
class LiteralStyle:
    """How a dialect spells inlined literals (display SQL; built from a ``DialectProfile``).

    ``doubled_quote_escape``: the dialect's lexer reads ``''`` inside a string literal as one
    ``'``; when it does not, a string holding ``'`` is refused (``FL507``).
    ``string_prefix``: written before a quoted string (``N`` for T-SQL national literals).
    ``true_literal``/``false_literal``: the boolean spellings.
    ``date_literal``: template with a ``{date}`` slot (``YYYY-MM-DD``).
    ``timestamp_literal``: template with ``{date}`` and ``{time}`` slots (UTC fields,
    ``HH:MM:SS`` plus ``.ffffff`` when the microseconds are not zero).
    Slots are filled with digits and separators only, never with a value's own text.
    """

    doubled_quote_escape: bool = True
    string_prefix: str = ""
    true_literal: str = "TRUE"
    false_literal: str = "FALSE"
    date_literal: str = "DATE '{date}'"
    timestamp_literal: str = "TIMESTAMP '{date}T{time}+00:00'"


ANSI_STYLE = LiteralStyle()


def _unsafe(what: str, why: str) -> RenderError:
    return RenderError(f"FL507 cannot inline {what}{why}")


def _refuse_unsafe_text(text: str, what: str) -> None:
    """Refuse text whose meaning in a string literal depends on server settings or on the
    console tool the SQL is pasted into."""
    if "\\" in text or "\x00" in text:
        raise _unsafe(
            what,
            ": it contains a backslash or NUL, whose meaning in a string literal depends on "
            "the server; bind it as a parameter instead",
        )
    if any(ch in text for ch in _LINE_TERMINATORS):
        raise _unsafe(
            what,
            ": it contains a line terminator, which console tools may treat as the end of a "
            "statement (SQL*Plus ends one at a blank line); bind it as a parameter instead",
        )


# CR, LF, NEL, LINE SEPARATOR, PARAGRAPH SEPARATOR: never inside an inlined literal
_LINE_TERMINATORS = ("\r", "\n", "\x85", "\u2028", "\u2029")


def ansi_quote(text: str) -> str:
    """ANSI string literal: single quotes, inner quotes doubled.

    Text holding a backslash, NUL or a line terminator (CR, LF, U+0085, U+2028, U+2029) is
    refused with ``FL507``: some servers (MySQL by default, PostgreSQL with
    ``standard_conforming_strings`` off, BigQuery, Databricks, ClickHouse) read a backslash
    as an escape, so no single spelling is safe everywhere, and console tools may end a
    statement at a line break (SQL*Plus at a blank line).
    """
    if type(text) is not str:
        raise _unsafe(f"a {type(text).__name__}", ": only an exact str is quoted")
    _refuse_unsafe_text(text, "a string")
    return "'" + str.replace(text, "'", "''") + "'"


def _is(t: type, *types: type) -> bool:
    # identity, never ``in``/``==``: a metaclass can make a class compare equal to ``int``
    return any(t is k for k in types)


_SCALARS: tuple[type, ...] = (bool, int, float, Decimal, str, dt.date, dt.datetime)
_NUMBERS: tuple[type, ...] = (int, float, Decimal)
_FAMILY_TYPES: dict[str, tuple[type, ...]] = {
    "boolean": (bool,),
    "integer": _NUMBERS,
    "decimal": _NUMBERS,
    "float": _NUMBERS,
    "string": (str,),
    "date": (dt.date,),
    "timestamp": (dt.datetime,),
}


def _date_text(d: dt.date) -> str:
    return f"{d.year:04d}-{d.month:02d}-{d.day:02d}"


def _time_text(t: dt.datetime) -> str:
    text = f"{t.hour:02d}:{t.minute:02d}:{t.second:02d}"
    return text + (f".{t.microsecond:06d}" if t.microsecond else "")


def _decimal_precision(plain: str) -> int:
    """Integer digits + scale of a plain-notation decimal (what ``DECIMAL(p, s)`` must hold):
    ``1E+50`` counts 51, ``0.001`` counts 3, ``0`` counts 1."""
    whole, _, scale = plain.lstrip("-").partition(".")
    return max(len(whole.lstrip("0")) + len(scale), 1)


def _significant_digits(text: str) -> int:
    """Significant digits of a ``float.__repr__`` (``1e+22`` -> 1, ``0.0001`` -> 1)."""
    mantissa = text.lstrip("-").split("e")[0].replace(".", "")
    return max(len(mantissa.strip("0")), 1)


def _trusted_tz(tz: object) -> bool:
    if tz is None or type(tz) is dt.timezone:
        return True
    try:
        import zoneinfo
    except ImportError:  # pragma: no cover - stdlib since 3.9
        return False
    return type(tz) is zoneinfo.ZoneInfo


def _inline_value(p: Param, quote_string: Callable[[str], str], style: LiteralStyle) -> str:
    """One literal that means exactly ``p.value`` in the target dialect, or ``FL507``.

    Only values of an exact built-in type are inlined, never a subclass (whose dunders could
    say anything): ``None``, ``bool``, ``int``, ``float``, ``Decimal``, ``str``, ``date`` and
    ``datetime``, each only in a matching parameter family, rendered through the base type's
    own functions (``int.__repr__``, ``float.__repr__``, ``Decimal.__format__``) or from its
    fields. Everything else is refused, never stringified.
    """
    v = p.value
    if v is None:
        return "NULL"
    t = type(v)
    what = f"{t.__name__} parameter {p.name}"
    if not _is(t, *_SCALARS):
        raise _unsafe(
            what,
            ": only exact bool, int, float, Decimal, str, date and datetime values are "
            "inlined (subclasses, enums and foreign scalar types are refused)",
        )
    if not _is(t, *_FAMILY_TYPES.get(p.family, ())):
        raise _unsafe(what, f": its type does not match the {p.family} family")
    if t is bool:
        return style.true_literal if v else style.false_literal
    if t is int:
        assert isinstance(v, int)
        # int.__lt__, never ``<`` on the value (an exact int, but stay with the base type)
        if int.__lt__(v, INT64_MIN) or int.__gt__(v, INT64_MAX):
            raise _unsafe(
                what,
                ": it is outside the signed 64-bit range, where engines change the literal's "
                "type; bind it as a parameter instead",
            )
        return int.__repr__(v)
    if t is float:
        assert isinstance(v, float)
        if not math.isfinite(v):
            raise _unsafe(what, ": NaN and infinities have no portable literal")
        text = float.__repr__(v)
        # repr is the shortest round-tripping form: never more than 17 significant digits
        assert _significant_digits(text) <= MAX_FLOAT_SIGNIFICANT_DIGITS, text
        return text
    if t is Decimal:
        assert isinstance(v, Decimal)
        if not Decimal.is_finite(v):
            raise _unsafe(what, ": NaN and infinities have no portable literal")
        digits, exponent = Decimal.as_tuple(v)[1:]
        assert isinstance(exponent, int)
        if len(digits) + abs(exponent) > MAX_LITERAL_DIGITS:
            raise _unsafe(what, f": its plain form has more than {MAX_LITERAL_DIGITS} digits")
        text = Decimal.__format__(v, "f")
        precision = _decimal_precision(text)
        if precision > MAX_DECIMAL_PRECISION:
            raise _unsafe(
                what,
                f": its plain form needs precision {precision}, more than the "
                f"{MAX_DECIMAL_PRECISION} digits a DECIMAL holds; bind it as a parameter instead",
            )
        return text
    if t is str:
        assert isinstance(v, str)
        _refuse_unsafe_text(v, what)
        try:
            str.encode(v, "utf-8")
        except UnicodeEncodeError:
            raise _unsafe(what, ": it is not encodable as UTF-8 (a lone surrogate)") from None
        if not style.doubled_quote_escape and "'" in v:
            raise _unsafe(
                what,
                ": it contains ' and the dialect's string literals have no doubled-quote form",
            )
        quoted = quote_string(v)
        if type(quoted) is not str:
            raise _unsafe(what, ": the quote function did not return a str")
        return style.string_prefix + quoted
    if t is dt.date:
        assert isinstance(v, dt.date)
        return style.date_literal.format(date=_date_text(v))
    assert t is dt.datetime and isinstance(v, dt.datetime)
    if not _trusted_tz(v.tzinfo):
        raise _unsafe(what, ": only datetime.timezone and zoneinfo time zones are inlined")
    aware = v if v.tzinfo is not None else dt.datetime.replace(v, tzinfo=dt.timezone.utc)
    try:
        utc = dt.datetime.astimezone(aware, dt.timezone.utc)
    except (OverflowError, ValueError):
        raise _unsafe(what, ": it is out of range in UTC") from None
    return style.timestamp_literal.format(date=_date_text(utc), time=_time_text(utc))


def style_for(dialect: str) -> LiteralStyle:
    """The literal spellings of ``dialect`` as the renderer resolves it (registry lookup)."""
    from factlang.sql.registry import literal_style_for  # registry imports this module

    return literal_style_for(dialect)


def inline_template(
    template: str,
    params: tuple[Param, ...],
    quote_string: Callable[[str], str] | None = None,
    style: LiteralStyle | None = None,
) -> str:
    """Display-oriented SQL with every parameter inlined as a literal.

    FactLang never executes it, but it must be safe if a host does: each value either
    becomes a literal whose meaning does not depend on string-escape settings
    (backslash handling, ``standard_conforming_strings``), or the whole call fails closed
    with ``RenderError("FL507 cannot inline ...")``. Strings are quoted by doubling ``'``;
    nothing ever relies on backslash escaping, and no literal holds a line terminator.
    Timestamps inline as UTC literals of the dialect's timestamp type: with an explicit
    ``UTC`` zone where the style says so (Trino, Presto, Athena), otherwise a literal most
    engines read as a naive (time-zone-less) UTC value, which compared with a
    time-zone-aware column depends on the session time zone, exactly as for the bound
    parameter. An ``int`` outside the signed 64-bit range and a ``Decimal`` needing more
    than 38 digits of precision are refused (engines change such a literal's type).
    ``style`` gives the dialect's spellings (default: ANSI); any other exception raised
    while converting a value (by a host quote function, say) is reported as ``FL507`` as
    well.
    """
    quote = quote_string if quote_string is not None else ansi_quote
    spelling = style if style is not None else ANSI_STYLE
    by_name = {p.name: p for p in params}

    def sub(m: re.Match[str]) -> str:
        name = m.group(1)
        if name not in by_name:
            raise RenderError(f"FL500 template references unknown parameter {name!r}")
        p = by_name[name]
        try:
            return _inline_value(p, quote, spelling)
        except RenderError:
            raise
        except Exception as exc:  # any conversion failure fails closed (FL507)
            raise _unsafe(
                f"{type(p.value).__name__} parameter {name}",
                f": converting it raised {type(exc).__name__}",
            ) from None

    return _TOKEN.sub(sub, template)


@dataclass(frozen=True)
class RenderedProbe:
    """One executable ``SELECT`` for one plan: neutral template + typed parameters.

    ``plan`` is ``None`` only for host-authored metadata SQL (``connectors.run_metadata``);
    every probe rendered by FactLang carries the plan it was rendered from.
    """

    sql_template: str
    params: tuple[Param, ...]
    arity: Arity
    plan: ProbePlan | None
    dialect: str
    tier: str

    def __post_init__(self) -> None:
        if isinstance(self.params, list):
            object.__setattr__(self, "params", tuple(self.params))
        check_params(self.params)
        check_template(self.sql_template, self.params)
        if not isinstance(self.arity, Arity):
            raise TypeError(f"arity must be an Arity, got {self.arity!r}")

    def bind(self, paramstyle: str) -> tuple[str, tuple[object, ...] | dict[str, object]]:
        return bind_template(self.sql_template, self.params, paramstyle)

    def inline(
        self, quote_string: Callable[[str], str] | None = None, style: LiteralStyle | None = None
    ) -> str:
        """Display SQL with literals inlined, spelled for ``self.dialect`` unless ``style``
        says otherwise; ``FL507`` when a value cannot be inlined safely."""
        spelling = style if style is not None else style_for(self.dialect)
        return inline_template(self.sql_template, self.params, quote_string, spelling)


__all__ = [
    "ANSI_STYLE",
    "MAX_LITERAL_DIGITS",
    "PARAMSTYLES",
    "LiteralStyle",
    "RenderError",
    "RenderedProbe",
    "ansi_quote",
    "bind_template",
    "check_params",
    "check_template",
    "inline_template",
    "placeholder",
    "style_for",
]
