"""sqlglot bridge: the internal SQL AST printed for any sqlglot dialect (spec 8.3, 8.5; layer 12).

Optional (extra ``factlang[sqlglot]``): sqlglot is imported lazily inside functions only, so
importing this module never needs it at import time of the core. Every probe it renders has
tier ``best_effort``.

The bridge converts the same tree :mod:`factlang.sql.lower` builds for the native printer
into a sqlglot expression and lets sqlglot spell it for the target dialect. It keeps every
native safety rule (spec 8.4):

- bind parameters are written as the same neutral ``{{name}}`` tokens as the native
  printer (a sqlglot ``Var`` whose text is the token, emitted verbatim by every dialect), so
  :meth:`~factlang.sql.probe.RenderedProbe.bind` works unchanged; a token sqlglot dropped or
  altered is ``FL500``;
- identifiers: unquoted -> unquoted (reserved words quoted after the dialect's case fold),
  quoted -> quoted with the dialect's quote character, inner quote doubled; NUL, newline,
  ``;``, ``{{``/``}}``, an over-long or an "unquoted" non-plain name is ``FL502``;
- a table name with more than ``max_table_parts`` parts (default 3) is ``FL505``;
- the rendered text must start with ``SELECT``/``WITH`` and contain no ``;``.

When the target also has a native FactLang profile (``prefer="sqlglot"`` cross-checks), the
bridge takes that profile's limits and FactLang-owned spellings (cast types, ``now``, the
keyhash expression, sample declarations) and lets sqlglot re-spell them; its quoted
identifiers (incl. ``quote_escapes``) and its information_schema query (qualified catalog
views, per-kind match modes) are the native printer's own text, spliced verbatim, so both
paths mean the same thing.

Sampling (spec 19 B2; 0.1.0 ruling): sampling is rendered only for a dialect with a native
profile, from that profile's own declarations: ``keyhash`` from its ``keyhash_expr``
(re-spelled by sqlglot), ``system``/``bernoulli`` from its ``sample_styles`` (the native
printer's FROM item, spliced verbatim; a ``None`` style is ``FL506``). Every dialect without a
native profile is ``FL506`` for every method: no engine verification exists for them.
"""

from __future__ import annotations

import dataclasses
import functools
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from factlang.ir import ProbePlan
from factlang.semantics.filters import Param
from factlang.semantics.refs import Ident, TableRef
from factlang.sql import ast as A
from factlang.sql.fragment import RenderedFragment
from factlang.sql.lower import Lowerer
from factlang.sql.printer import Printer, guard
from factlang.sql.probe import ANSI_STYLE, LiteralStyle, RenderedProbe, RenderError, placeholder
from factlang.sql.profile import DialectProfile
from factlang.sql.registry import SQLGLOT_NAMES, get_profile, native_dialects

if TYPE_CHECKING:  # pragma: no cover - typing only; sqlglot is imported lazily at runtime
    from sqlglot import exp

TIER = "best_effort"
DEFAULT_MAX_TABLE_PARTS = 3
# No sqlglot dialect states its identifier limit; 128 is the common one (ANSI, Teradata,
# Hive). A longer name is refused rather than risk a silent truncation.
DEFAULT_MAX_IDENTIFIER_LENGTH = 128
# Quoted when written unquoted, on top of sqlglot's own per-dialect reserved keywords.
_ANSI_WORDS = """
all and any as asc between both by case cast check column constraint create cross
current_date current_time current_timestamp default delete desc distinct drop else end
except exists false fetch for foreign from full grant group having in inner insert
intersect into is join leading left like limit natural not null of on or order outer
primary references right select some table then to trailing true union unique update
user using values when where with
"""
_ANSI_RESERVED = frozenset(_ANSI_WORDS.split())
# Long-tail cast types; sqlglot re-spells them per dialect (e.g. TEXT -> STRING in Hive).
_CAST_TYPES = {
    "integer": "BIGINT",
    "decimal": "DECIMAL(38, 9)",
    "float": "DOUBLE",
    "string": "TEXT",
    "boolean": "BOOLEAN",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "time": "TIME",
    "bytes": "VARBINARY",
    "json": "JSON",
}
# Inline-literal spellings of bridged dialects with no native profile, by sqlglot dialect
# name (spec 0.1.3 I1/I2). Consulted after native profiles and before the ancestor walk, for
# the dialect class registered under exactly that name (a subclass walks as before). Display
# SQL only; internal (not part of the public API). The quote-doubling decision always comes
# from the dialect's lexer, never from here: literal_style() replaces every entry's
# doubled_quote_escape with the lexer's answer. The hive/spark/spark2 entries say False
# anyway (their lexers have no '' form), so the table never reads as claiming one.
_UTC_TIMESTAMP = "TIMESTAMP '{date} {time} UTC'"  # Presto/Trino: an explicit zone
_NAIVE_TIMESTAMP = "TIMESTAMP '{date} {time}'"  # naive, holding UTC wall-clock time
_NAIVE_NO_DOUBLING = dataclasses.replace(
    ANSI_STYLE, timestamp_literal=_NAIVE_TIMESTAMP, doubled_quote_escape=False
)
BRIDGE_LITERAL_STYLES: Mapping[str, LiteralStyle] = MappingProxyType(
    {
        "presto": dataclasses.replace(ANSI_STYLE, timestamp_literal=_UTC_TIMESTAMP),
        "athena": dataclasses.replace(ANSI_STYLE, timestamp_literal=_UTC_TIMESTAMP),
        "hive": _NAIVE_NO_DOUBLING,
        "spark": _NAIVE_NO_DOUBLING,
        "spark2": _NAIVE_NO_DOUBLING,
        # Teradata has no boolean literal: 1/0, as on T-SQL and Oracle
        "teradata": dataclasses.replace(
            ANSI_STYLE, true_literal="1", false_literal="0", timestamp_literal=_NAIVE_TIMESTAMP
        ),
        "exasol": dataclasses.replace(ANSI_STYLE, timestamp_literal=_NAIVE_TIMESTAMP),
    }
)
_GENERIC_CATALOG = "SELECT column_name, data_type FROM information_schema.columns WHERE {where}"
_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_TOKEN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")
_FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("\x00", "NUL"),
    ("\n", "a newline"),
    ("\r", "a carriage return"),
    ("{{", "'{{'"),
    ("}}", "'}}'"),
    (";", "';'"),
)
_S_MARKER = "fl_keyhash_input"
_WHERE_MARKER = "fl_where_marker"
_FRAGMENT_MARKER = "fl_fragment_marker"


def _exp() -> Any:
    from sqlglot import exp

    return exp


# -- target ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Target:
    name: str  # as the caller gave it
    sqlglot: str  # sqlglot dialect name
    profile: DialectProfile | None
    max_table_parts: int
    max_identifier_length: int


def _sqlglot_dialect(name: str) -> Any:
    from sqlglot.dialects.dialect import Dialect

    try:
        return Dialect.get_or_raise(name)
    except ValueError:
        return None


def supports_dialect(dialect: str) -> bool:
    """True when ``dialect`` (a FactLang or sqlglot dialect name) is one sqlglot knows."""
    if not isinstance(dialect, str):
        return False
    return _sqlglot_dialect(SQLGLOT_NAMES.get(dialect, dialect)) is not None


def lexer_doubles_quote(dialect: str) -> bool | None:
    """Whether sqlglot's lexer for ``dialect`` reads ``''`` in a string as one ``'``; ``None``
    when sqlglot knows no dialect of that name."""
    if not isinstance(dialect, str):
        return None
    resolved = _sqlglot_dialect(SQLGLOT_NAMES.get(dialect, dialect))
    if resolved is None:
        return None
    return "'" in type(resolved).tokenizer_class.STRING_ESCAPES


def literal_style(dialect: str) -> LiteralStyle | None:
    """Inline-literal spellings for a dialect sqlglot knows, or ``None`` when it knows none.

    The quote form comes from the dialect's own lexer (``''`` is an escaped quote only when
    sqlglot's tokenizer lists ``'`` among its string escapes); the other spellings come from
    the ``BRIDGE_LITERAL_STYLES`` entry of the dialect's sqlglot name, else from the nearest
    native profile in the dialect's class hierarchy (Fabric -> T-SQL, Materialize ->
    Postgres, Dune -> Trino), else ANSI.
    """
    if not isinstance(dialect, str):
        return None
    resolved = _sqlglot_dialect(SQLGLOT_NAMES.get(dialect, dialect))
    if resolved is None:
        return None
    doubled = lexer_doubles_quote(dialect) is True
    entry = _bridge_entry(type(resolved))
    if entry is not None:
        return dataclasses.replace(entry, doubled_quote_escape=doubled)
    by_class: dict[type, DialectProfile] = {}
    for name in native_dialects():
        native = _sqlglot_dialect(SQLGLOT_NAMES.get(name, name))
        profile = get_profile(name)
        if native is not None and profile is not None:
            by_class.setdefault(type(native), profile)
    base = ANSI_STYLE
    for cls in type(resolved).__mro__:
        if cls in by_class:
            base = by_class[cls].literal_style
            break
    return dataclasses.replace(base, doubled_quote_escape=doubled)


def _bridge_entry(cls: type) -> LiteralStyle | None:
    """The ``BRIDGE_LITERAL_STYLES`` entry whose sqlglot dialect class is exactly ``cls``."""
    from sqlglot.dialects.dialect import Dialect

    for name, style in BRIDGE_LITERAL_STYLES.items():
        if Dialect.classes.get(name) is cls:
            return style
    return None


def _target(dialect: str | DialectProfile, max_table_parts: int | None = None) -> _Target:
    """Resolve ``dialect``; a ``DialectProfile`` object is used as given (never swapped)."""
    if isinstance(dialect, DialectProfile):
        profile: DialectProfile | None = dialect
        dialect = dialect.name
    else:
        profile = get_profile(dialect)
    if not supports_dialect(dialect):
        raise RenderError(
            f"FL504 unknown dialect {dialect!r}; native dialects: {', '.join(native_dialects())}; "
            "the sqlglot bridge (pip install factlang[sqlglot]) knows no dialect of that name"
        )
    if max_table_parts is None:
        max_table_parts = profile.max_table_parts if profile else DEFAULT_MAX_TABLE_PARTS
    if isinstance(max_table_parts, bool) or not isinstance(max_table_parts, int):
        raise ValueError(f"max_table_parts must be a positive int, got {max_table_parts!r}")
    if max_table_parts < 1:
        raise ValueError(f"max_table_parts must be a positive int, got {max_table_parts!r}")
    return _Target(
        dialect,
        SQLGLOT_NAMES.get(dialect, dialect),
        profile,
        max_table_parts,
        profile.max_identifier_length if profile else DEFAULT_MAX_IDENTIFIER_LENGTH,
    )


@functools.lru_cache(maxsize=256)
def _parse_cached(sql: str, dialect: str) -> Any:
    import sqlglot

    return sqlglot.parse_one(sql, read=dialect)


def _parse(sql: str, dialect: str) -> Any:
    """Parse FactLang-owned SQL text (a profile spelling); a fresh copy every call."""
    try:
        return _parse_cached(sql, dialect).copy()
    except Exception as exc:  # sqlglot.errors.ParseError and friends
        raise RenderError(f"FL500 sqlglot cannot parse {sql!r} as {dialect}: {exc}") from None


# -- conversion --------------------------------------------------------------------------------


class _Converter:
    """Internal AST -> sqlglot expression; collects parameters (first-use order is by text)."""

    def __init__(self, target: _Target | None) -> None:
        self.exp = _exp()
        self.target = target
        self.params: dict[str, Param] = {}
        self._reserved_names: set[str] = set()
        self._catalog_count = 0
        self._derived = 0
        self._keywords: frozenset[str] = _ANSI_RESERVED
        self._dialect: Any = None
        if target is not None:
            self._dialect = _sqlglot_dialect(target.sqlglot)
            generated: set[str] = getattr(self._dialect.generator_class, "RESERVED_KEYWORDS", set())
            native = target.profile.reserved_words if target.profile else frozenset()
            self._keywords = (
                _ANSI_RESERVED | {w.lower() for w in generated} | {w.lower() for w in native}
            )

    def reserve(self, node: object) -> None:
        self._reserved_names |= {n.param.name for n in A.walk(node) if isinstance(n, A.ParamRef)}

    # -- identifiers ---------------------------------------------------------------------

    def check_ident(self, text: str, quoted: bool) -> None:
        limit = self.target.max_identifier_length if self.target else DEFAULT_MAX_IDENTIFIER_LENGTH
        where = self.target.name if self.target else "the sqlglot bridge"
        for bad, what in _FORBIDDEN:
            if bad in text:
                raise RenderError(f"FL502 identifier {text!r} contains {what}")
        if len(text.encode("utf-8")) > limit:
            raise RenderError(
                f"FL502 identifier {text[:40]!r}… is longer than {limit} bytes, the {where} limit"
            )
        if not text:
            raise RenderError("FL502 empty identifier")
        if not quoted and not _PLAIN.fullmatch(text):
            raise RenderError(f"FL502 unquoted identifier {text!r} is not a plain name")

    def _fold(self, text: str) -> str:
        """What an unquoted identifier resolves to in the target (spec 8.4 reserved rule)."""
        if self.target is not None and self.target.profile is not None:
            return self.target.profile.fold(text)
        if self._dialect is None:
            return text
        folded = self._dialect.normalize_identifier(self.exp.to_identifier(text, quoted=False))
        return str(folded.this)

    def ident(self, i: Ident) -> Any:
        if not isinstance(i, Ident):
            raise TypeError(f"expected an Ident, got {i!r}")
        self.check_ident(i.text, i.quoted)
        if i.quoted:
            return self._quoted(i.text)
        if i.text.lower() in self._keywords:
            return self._quoted(self._fold(i.text))
        return self.exp.Identifier(this=i.text, quoted=False)

    def _quoted(self, text: str) -> Any:
        """A quoted identifier; a native target's own quoting (incl. ``quote_escapes``)."""
        profile = self.target.profile if self.target is not None else None
        if profile is not None:  # spelled exactly as the native printer does
            return self.exp.Var(this=profile.quote_ident(text))
        return self.exp.Identifier(this=text, quoted=True)

    def alias(self, name: str) -> Any:
        """A FactLang-chosen (or caller-given) alias: exact text, quoted only if reserved."""
        self.check_ident(name, False)
        if name.lower() in self._keywords:
            return self._quoted(name)
        return self.exp.Identifier(this=name, quoted=False)

    def table(self, ref: TableRef, alias: str | None = None) -> Any:
        parts = ref.parts()
        limit = self.target.max_table_parts if self.target else DEFAULT_MAX_TABLE_PARTS
        if len(parts) > limit:
            where = self.target.name if self.target else "the sqlglot bridge"
            raise RenderError(
                f"FL505 table {'.'.join(x.text for x in parts)!r} has {len(parts)} name parts; "
                f"{where} accepts at most {limit}"
            )
        e = self.exp
        args: dict[str, Any] = {"this": self.ident(ref.table)}
        if ref.schema is not None:
            args["db"] = self.ident(ref.schema)
        if ref.catalog is not None:
            args["catalog"] = self.ident(ref.catalog)
        if alias is not None:
            args["alias"] = e.TableAlias(this=self.alias(alias))
        return e.Table(**args)

    # -- parameters ----------------------------------------------------------------------

    def param(self, p: Param) -> Any:
        prior = self.params.setdefault(p.name, p)
        if prior != p:
            raise RenderError(f"FL500 parameter name {p.name!r} is bound to two different values")
        return self.exp.Var(this=placeholder(p.name))

    def _catalog_param(self, value: str) -> Any:
        while True:
            self._catalog_count += 1
            name = f"cat{self._catalog_count}"
            if name not in self._reserved_names and name not in self.params:
                break
        return self.param(Param(name, value, "string"))

    @property
    def _clickhouse(self) -> bool:
        return self.target is not None and self.target.sqlglot == "clickhouse"

    def _derived_alias(self) -> Any:
        self._derived += 1
        return self.exp.TableAlias(this=self.exp.Identifier(this=f"fl_d{self._derived}"))

    # -- expressions ---------------------------------------------------------------------

    def expr(self, x: object) -> Any:
        e = self.exp
        if isinstance(x, A.Column):
            col = self.ident(x.name)
            if x.qualifier:
                return e.Column(this=col, table=self.alias(x.qualifier))
            return e.Column(this=col)
        if isinstance(x, A.ParamRef):
            return self.param(x.param)
        if isinstance(x, A.Literal):
            if x.value is None:
                return e.Null()
            if isinstance(x.value, str):
                return e.Literal.string(x.value)
            return e.Literal.number(x.value)
        if isinstance(x, A.Star):
            return e.Star()
        if isinstance(x, A.Agg):
            return self.agg(x)
        if isinstance(x, A.CountIf):
            # rows where cond is TRUE; 0 (never NULL) over no rows, spelled the portable way
            case = e.Case(
                ifs=[e.If(this=self.expr(x.cond), true=e.Literal.number(1))],
                default=e.Literal.number(0),
            )
            return e.Coalesce(this=e.Sum(this=case), expressions=[e.Literal.number(0)])
        if isinstance(x, A.Lower):
            return e.Lower(this=self.expr(x.e))
        if isinstance(x, A.Trim):
            return e.Trim(this=self.expr(x.e))
        if isinstance(x, A.SplitPrefix):
            return self.split_prefix(x)
        if isinstance(x, A.Like):
            return e.Like(this=self.expr(x.e), expression=self.expr(x.pattern))
        if isinstance(x, A.Cast):
            if self._clickhouse and isinstance(x.e, A.Literal) and x.e.value is None:
                # as the native profile: Nullable(Nothing) unifies with UInt64 counts in a
                # UNION ALL, Nullable(Int64) does not (NO_COMMON_TYPE)
                return e.Null()
            return e.Cast(this=self.expr(x.e), to=self.cast_type(x.family))
        if isinstance(x, A.Coalesce):
            first, *rest = (self.expr(i) for i in x.items)
            return e.Coalesce(this=first, expressions=rest)
        if isinstance(x, A.Case):
            ifs = [e.If(this=self.expr(c), true=self.expr(r)) for c, r in x.whens]
            default = self.expr(x.else_) if x.else_ is not None else None
            return e.Case(ifs=ifs, default=default)
        if isinstance(x, A.Now):
            return self.now()
        if isinstance(x, A.Compare):
            ops: dict[str, Any] = {
                "=": e.EQ,
                "!=": e.NEQ,
                "<": e.LT,
                "<=": e.LTE,
                ">": e.GT,
                ">=": e.GTE,
            }
            return ops[x.op](this=self.expr(x.left), expression=self.expr(x.right))
        if isinstance(x, (A.And, A.Or)):
            cls = e.And if isinstance(x, A.And) else e.Or
            items = [self.expr(i) for i in x.items]
            out = items[0]
            for item in items[1:]:
                out = cls(this=out, expression=item)
            return e.Paren(this=out)
        if isinstance(x, A.Not):
            return e.Not(this=e.Paren(this=self.expr(x.e)))
        if isinstance(x, A.InList):
            inl = e.In(this=self.expr(x.e), expressions=[self.expr(i) for i in x.items])
            return e.Not(this=inl) if x.negated else inl
        if isinstance(x, A.IsNull):
            is_ = e.Is(this=self.expr(x.e), expression=e.Null())
            return e.Not(this=is_) if x.negated else is_
        if isinstance(x, A.Between):
            return e.Between(this=self.expr(x.e), low=self.expr(x.lo), high=self.expr(x.hi))
        if isinstance(x, A.KeyHash):
            return self.keyhash(x)
        raise RenderError(f"FL500 the sqlglot bridge cannot convert {type(x).__name__}")

    def agg(self, x: A.Agg) -> Any:
        e = self.exp
        if x.arg is None:
            return e.Count(this=e.Star())
        arg = self.expr(x.arg)
        if x.fn == "COUNT":
            return e.Count(this=e.Distinct(expressions=[arg]) if x.distinct else arg)
        inner = e.Distinct(expressions=[arg]) if x.distinct else arg
        if self._clickhouse:
            # as the native profile: of no rows these give 0/nan over a non-Nullable input
            return e.Anonymous(this=f"{x.fn.lower()}OrNull", expressions=[inner])
        cls = {"SUM": e.Sum, "AVG": e.Avg, "MIN": e.Min, "MAX": e.Max}[x.fn]
        return cls(this=inner)

    def split_prefix(self, x: A.SplitPrefix) -> Any:
        """``CASE WHEN STRPOS(e, sep) > 0 THEN SUBSTRING(e, 1, STRPOS(e, sep) - 1) ELSE e END``."""
        e = self.exp

        def pos() -> Any:
            return e.StrPosition(this=self.expr(x.e), substr=self.expr(x.separator))

        found = e.GT(this=pos(), expression=e.Literal.number(0))
        prefix = e.Substring(
            this=self.expr(x.e),
            start=e.Literal.number(1),
            length=e.Sub(this=pos(), expression=e.Literal.number(1)),
        )
        return e.Case(ifs=[e.If(this=found, true=prefix)], default=self.expr(x.e))

    def cast_type(self, family: str) -> Any:
        e = self.exp
        t = self.target
        if t is not None and t.profile is not None:
            native = t.profile.cast_types.get(family)
            if native is None:
                raise RenderError(f"FL503 cast to {family} is not expressible in {t.name}")
            try:
                return e.DataType.build(native, dialect=t.sqlglot)
            except Exception as exc:
                raise RenderError(
                    f"FL503 sqlglot cannot read {native!r} as a type: {exc}"
                ) from None
        if family not in _CAST_TYPES:
            raise RenderError(f"FL503 cast to {family} is not expressible through the bridge")
        return e.DataType.build(_CAST_TYPES[family])

    def now(self) -> Any:
        t = self.target
        if t is not None and t.profile is not None:
            return _parse(t.profile.now_sql, t.sqlglot)
        return self.exp.CurrentTimestamp()

    def keyhash(self, x: A.KeyHash) -> Any:
        """The native profile's ruled keyhash bucket (spec 19), re-spelled by sqlglot."""
        t = self.target
        template = t.profile.keyhash_expr if t is not None and t.profile is not None else None
        if t is None or template is None:
            name = t.name if t is not None else "(none)"
            raise RenderError(
                f"FL506 sample method keyhash is not supported by dialect {name}: the sqlglot "
                "bridge has a hash mapping only for the native keyhash dialects"
            )
        e = self.exp
        items = [e.Cast(this=self.expr(x.seed), to=self.cast_type("string"))]
        items += [self.expr(p) for p in x.parts]
        s = items[0]
        for item in items[1:]:
            s = e.DPipe(this=e.DPipe(this=s, expression=e.Literal.string("|")), expression=item)
        tree = _parse(template.format(s=_S_MARKER), t.sqlglot)
        markers = [c for c in tree.find_all(e.Column) if c.name == _S_MARKER]
        if len(markers) != 1:
            raise RenderError(f"FL500 keyhash template of {t.name} must use its input exactly once")
        markers[0].replace(e.Paren(this=s))
        return tree

    # -- relations -----------------------------------------------------------------------

    def _native_sampler(self, method: str) -> Printer:
        """The native printer that spells a sample (0.1.0: native profiles only), else FL506."""
        t = self.target
        if t is None or t.profile is None or not t.profile.supports_sample(method):
            name = t.name if t is not None else "(none)"
            why = "" if t is not None and t.profile is not None else " (no native profile)"
            raise RenderError(
                f"FL506 sample method {method} is not supported by dialect {name}{why}"
            )
        return Printer(t.profile)

    def table_sample(self, s: A.TableSample) -> Any:
        """The native profile's declared sample clause, verbatim (FL506 if none)."""
        return self.exp.Var(this=self._native_sampler(s.method).table_sample(s))

    def single_from(self, x: object) -> Any:
        e = self.exp
        if isinstance(x, A.TableName):
            table = self.table(x.ref, x.alias)  # the bridge's own identifier/part checks
            if x.sample is not None:
                # the profile's sample_styles, spelled by the native printer for that profile
                return e.Var(this=self._native_sampler(x.sample.method).from_item(x))
            return table
        if isinstance(x, A.SubqueryFrom):
            inner = (
                self.union(x.select) if isinstance(x.select, A.UnionAll) else self.select(x.select)
            )
            if (
                self._clickhouse
                and isinstance(x.select, A.Select)
                and _has_left_join(x.select.from_)
            ):
                # as the native profile: a LEFT JOIN fills unmatched columns with type
                # defaults (0, '') unless join_use_nulls, so orphans would read as matched
                one = e.EQ(this=e.var("join_use_nulls"), expression=e.Literal.number(1))
                inner.set("settings", [one])
            return e.Subquery(this=inner, alias=e.TableAlias(this=self.alias(x.alias)))
        if isinstance(x, A.CatalogColumns):
            inner = self.catalog_columns(x)
            if isinstance(inner, e.Var):  # native query text: splice the whole FROM item
                alias = self._derived_alias().this.name
                return e.Var(this=f"({inner.this}) {alias}")
            return e.Subquery(this=inner, alias=self._derived_alias())
        raise RenderError(
            f"FL500 the sqlglot bridge cannot convert {type(x).__name__} as a FROM item"
        )

    def from_parts(self, x: object) -> Any:
        """A left-deep join tree as sqlglot's FROM item plus its JOIN list."""
        e = self.exp
        if not isinstance(x, A.Join):
            return self.single_from(x), []
        base, joins = self.from_parts(x.left)
        if isinstance(x.right, A.Join):
            raise RenderError("FL500 the sqlglot bridge cannot convert a right-nested join")
        right = self.single_from(x.right)
        if x.on is None:
            joins.append(e.Join(this=right, kind="CROSS"))
        elif x.kind == "LEFT":
            joins.append(e.Join(this=right, on=self.expr(x.on), side="LEFT"))
        else:
            joins.append(e.Join(this=right, on=self.expr(x.on), kind="INNER"))
        return base, joins

    def catalog_columns(self, x: A.CatalogColumns) -> Any:
        """The information_schema query; names bound as string parameters.

        A native target gets the native printer's own query, verbatim (its templates,
        qualified catalog views, per-kind match modes and quoting); without a profile the
        bridge writes the generic ``information_schema.columns`` query: quoted names
        compare exactly, unquoted ones case-folded, an omitted schema is the current one.
        """
        e = self.exp
        t = self.target
        p = t.profile if t is not None else None
        ref = x.table
        limit = t.max_table_parts if t is not None else DEFAULT_MAX_TABLE_PARTS
        if len(ref.parts()) > limit:
            self.table(ref)  # raises FL505
        if p is not None:
            printer = Printer(p)
            text = printer.catalog_columns(x)
            for prm in printer.params:
                self.param(prm)  # FL500 if a plan parameter already uses that name
            return e.Var(this=text)
        conds: list[Any] = []

        def match(column: str, name: Ident) -> None:
            self.check_ident(name.text, name.quoted)
            col = e.Column(this=e.Identifier(this=column))
            if name.quoted:
                conds.append(e.EQ(this=col, expression=self._catalog_param(name.text)))
                return
            rhs = e.Lower(this=self._catalog_param(name.text))
            conds.append(e.EQ(this=e.Lower(this=col), expression=rhs))

        match("table_name", ref.table)
        if ref.schema is not None:
            match("table_schema", ref.schema)
        else:
            schema = e.Column(this=e.Identifier(this="table_schema"))
            current = getattr(e, "CurrentSchema", None)  # sqlglot < 26 has no node for it
            now = current() if current is not None else e.Anonymous(this="CURRENT_SCHEMA")
            conds.append(e.EQ(this=schema, expression=now))
        if ref.catalog is not None:
            match("table_catalog", ref.catalog)
        if x.column is not None:
            match("column_name", x.column)
        where = conds[0]
        for cond in conds[1:]:
            where = e.And(this=where, expression=cond)
        tree = _parse(_GENERIC_CATALOG.format(where=f"{_WHERE_MARKER} = 1"), "")
        markers = [
            eq
            for eq in tree.find_all(e.EQ)
            if isinstance(eq.this, e.Column) and eq.this.name == _WHERE_MARKER
        ]
        assert len(markers) == 1
        markers[0].replace(where)
        return tree

    # -- statements ----------------------------------------------------------------------

    def _ordered(self, item: A.OrderItem) -> Any:
        """``ORDER BY x ASC|DESC`` with the dialect's own NULL placement (as native prints)."""
        ordering = getattr(self._dialect, "NULL_ORDERING", "nulls_are_small")
        if ordering == "nulls_are_small":
            nulls_first = not item.descending
        elif ordering == "nulls_are_large":
            nulls_first = item.descending
        else:  # nulls_are_last
            nulls_first = False
        return self.exp.Ordered(
            this=self.expr(item.expr), desc=item.descending, nulls_first=nulls_first
        )

    def select(self, x: A.Select) -> Any:
        e = self.exp
        cols = [e.Alias(this=self.expr(c.expr), alias=self.alias(c.alias)) for c in x.columns]
        base, joins = self.from_parts(x.from_)
        sel = e.Select(expressions=cols).from_(base, copy=False)
        for j in joins:
            sel.append("joins", j)
        if x.where is not None:
            sel = sel.where(self.expr(x.where), copy=False)
        if x.group_by:
            sel = sel.group_by(*(self.expr(g) for g in x.group_by), copy=False)
        if x.order_by:
            sel = sel.order_by(*(self._ordered(o) for o in x.order_by), copy=False)
        if x.limit is not None:
            sel = sel.limit(x.limit, copy=False)
        return sel

    def union(self, x: A.UnionAll) -> Any:
        """Branches with ORDER BY/LIMIT become derived tables, so they never bind to the union."""
        e = self.exp
        branches: list[Any] = []
        for s in x.selects:
            branch: Any = self.select(s)
            if s.order_by or s.limit is not None:
                sub = e.Subquery(this=branch, alias=self._derived_alias())
                branch = e.Select(expressions=[e.Star()]).from_(sub, copy=False)
            branches.append(branch)
        out = branches[0]
        for b in branches[1:]:
            out = e.Union(this=out, expression=b, distinct=False)
        return out

    def statement(self, node: A.Select | A.UnionAll) -> Any:
        self.reserve(node)
        if isinstance(node, A.UnionAll):
            e = self.exp
            sub = e.Subquery(this=self.union(node), alias=self._derived_alias())
            return e.Select(expressions=[e.Star()]).from_(sub, copy=False)
        return self.select(node)

    def convert(self, node: object) -> Any:
        self.reserve(node)
        if isinstance(node, (A.Select, A.UnionAll)):
            return self.statement(node)
        if isinstance(node, A.TableSample):
            return self.table_sample(node)
        if isinstance(node, (A.TableName, A.SubqueryFrom, A.CatalogColumns)):
            return self.single_from(node)
        if isinstance(node, A.Join):
            base, joins = self.from_parts(node)
            sel = self.exp.select(self.exp.Star()).from_(base, copy=False)
            for j in joins:
                sel.append("joins", j)
            return sel
        return self.expr(node)


def _has_left_join(item: object) -> bool:
    if isinstance(item, A.Join):
        return item.kind == "LEFT" or _has_left_join(item.left) or _has_left_join(item.right)
    return False


# -- generation and assembly -------------------------------------------------------------------


def _generate(tree: Any, dialect: str) -> str:
    """sqlglot's text for ``tree`` in ``dialect``; anything it cannot express is FL500."""
    from sqlglot.errors import ErrorLevel, UnsupportedError

    try:
        text: str = tree.sql(dialect=dialect, unsupported_level=ErrorLevel.RAISE)
    except UnsupportedError as exc:
        raise RenderError(f"FL500 sqlglot cannot express the probe in {dialect}: {exc}") from None
    return text


def _ordered_params(text: str, params: dict[str, Param]) -> tuple[Param, ...]:
    """Parameters in first-use order of their tokens; unused ones last (check_template flags)."""
    order = list(dict.fromkeys(m.group(1) for m in _TOKEN.finditer(text)))
    used = [params[n] for n in order if n in params]
    unused = [p for n, p in params.items() if n not in order]
    return (*used, *unused)


def to_sqlglot(node: object, dialect: str | None = None) -> exp.Expression:
    """Convert an internal SQL AST node to a sqlglot expression.

    Bind parameters become ``{{name}}`` tokens (sqlglot ``Var``). Without ``dialect`` the
    conversion uses the generic spellings and cannot express ``KeyHash`` or ``TableSample``
    (FL506), which need a target.
    """
    target = _target(dialect) if dialect is not None else None
    converted: exp.Expression = _Converter(target).convert(node)
    return converted


def render_sqlglot(
    plan: ProbePlan, dialect: str | DialectProfile, *, max_table_parts: int | None = None
) -> RenderedProbe:
    """Lower ``plan`` and print it through sqlglot for ``dialect`` (tier ``best_effort``).

    ``dialect`` is a sqlglot dialect name or a FactLang native name (mapped through
    ``SQLGLOT_NAMES``). ``max_table_parts`` defaults to the native profile's, else 3.
    """
    target = _target(dialect, max_table_parts)
    tree = Lowerer().lower(plan)
    A.params_of(tree)  # FL500 when one parameter name maps to two different values
    conv = _Converter(target)
    sql = _generate(conv.statement(tree), target.sqlglot)
    if target.profile is not None:  # e.g. ClickHouse SETTINGS, as on the native path
        sql += target.profile.statement_suffix
    guard(sql)
    params = _ordered_params(sql, conv.params)
    return RenderedProbe(sql, params, plan.arity, plan, target.name, TIER)


# The hook factlang.sql.registry.renderer_for calls: render(plan, dialect).
render: Callable[..., RenderedProbe] = render_sqlglot


def _fragment(text: str, conv: _Converter, dialect: str) -> RenderedFragment:
    if ";" in text:
        raise RenderError(f"FL500 rendered fragment failed the guard: {text[:80]!r}")
    return RenderedFragment(text, _ordered_params(text, conv.params), dialect, TIER)


def render_filter_fragment(
    node: A.Expr, dialect: str | DialectProfile, *, table: TableRef | None = None
) -> RenderedFragment:
    """A lowered filter (boolean AST expression) as a bindable fragment for ``dialect``."""
    target = _target(dialect)
    conv = _Converter(target)
    if table is not None:
        conv.table(table)  # FL502 / FL505
    conv.reserve(node)
    return _fragment(_generate(conv.expr(node), target.sqlglot), conv, target.name)


def render_join_fragment(
    from_: A.FromItem, where: A.Expr | None, dialect: str | DialectProfile
) -> RenderedFragment:
    """``FROM a JOIN b ON … [WHERE …]`` for a lowered join path, bindable, for ``dialect``."""
    target = _target(dialect)
    conv = _Converter(target)
    conv.reserve(from_)
    if where is not None:
        conv.reserve(where)
    e = conv.exp
    base, joins = conv.from_parts(from_)
    sel = e.select(e.Column(this=e.Identifier(this=_FRAGMENT_MARKER))).from_(base, copy=False)
    for j in joins:
        sel.append("joins", j)
    if where is not None:
        sel = sel.where(conv.expr(where), copy=False)
    text = _generate(sel, target.sqlglot)
    head = f"SELECT {_FRAGMENT_MARKER} "
    if not text.startswith(head) or not text[len(head) :].startswith("FROM "):
        raise RenderError(f"FL500 sqlglot spelled the join fragment unexpectedly: {text[:80]!r}")
    return _fragment(text[len(head) :], conv, target.name)


__all__ = [
    "DEFAULT_MAX_IDENTIFIER_LENGTH",
    "DEFAULT_MAX_TABLE_PARTS",
    "TIER",
    "render",
    "render_filter_fragment",
    "render_join_fragment",
    "render_sqlglot",
    "supports_dialect",
    "to_sqlglot",
]
