"""Dialect profiles: the small decisions a native printer needs (spec 8.3, 8.4, 19 B2; layer 12).

A :class:`DialectProfile` is data, not code: quoting, case folding, reserved words, how to
count conditionally, how to limit, how to cast, how to sample and how to ask the catalog for
a table's columns. The printer (:mod:`factlang.sql.printer`) reads it; ``overrides`` maps an
internal-AST node type to a print function for the rare node a profile must spell
differently.

Template fields are FactLang-owned SQL text with ``{name}`` slots, validated here:

- ``sample_styles[method]`` (``system``/``bernoulli``): suffix written after the table name
  and alias; slots ``{percent}`` (fraction x 100, plain decimal), ``{fraction}`` and
  ``{seed}``. ``None`` or a missing method means the dialect cannot express it (FL506).
- ``keyhash_expr``: the keyhash *bucket* in ``[0, 1_000_000)`` of the hash input ``{s}``
  (spec 19 keyhash ruling); ``None`` means keyhash is not supported (FL506).
- ``information_schema_sql``: a ``SELECT column_name, data_type …`` with one ``{where}`` slot.
- ``qualified_information_schema_sql`` (optional): the same query with a ``{prefix}`` slot
  where the catalog view must be qualified, used when the ref names the part that owns the
  view (``information_schema_scope``: ``catalog`` -> ``<catalog>.``, e.g. Snowflake/T-SQL;
  ``schema`` -> ``[<catalog>.]<schema>.``, BigQuery datasets). The prefix is printed with the
  profile's own identifier rules.
- ``default_schema_sql`` / ``default_catalog_sql``: the right-hand side of ``table_schema =``
  / ``table_catalog =`` used when a ref has no schema / catalog (e.g. ``current_schema()``,
  or ``ANY (current_schemas(false))``).

``quoted_catalog_match`` / ``unquoted_catalog_match`` say how CatalogColumns compares a name
with the catalog, so that it finds what the same identifier finds in SQL: ``exact``;
``lower``/``upper`` (fold the name, then compare exactly); ``casefold`` (``LOWER(col) =
LOWER(name)``, for dialects that resolve identifiers case-insensitively). Column names may
follow a different rule (``quoted_column_match`` / ``unquoted_column_match``; ``None`` means
the table rule), e.g. BigQuery tables are case-sensitive but its columns are not.

``quote_escapes`` (optional) maps characters to their spelling inside a quoted identifier for
dialects where the plain doubling of the closing quote is wrong: in BigQuery a backslash and a
backtick are each written after a backslash; in ClickHouse a backslash is an escape too. It
must cover the closing quote.
"""

from __future__ import annotations

import re
import string
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal

from factlang.semantics.filters import PARAM_FAMILIES
from factlang.semantics.types import TYPE_FAMILIES

CountIfStyle = Literal["countif", "count_if", "sum_case", "filter"]
LimitStyle = Literal["limit", "top", "fetch"]
UnquotedCase = Literal["upper", "lower", "preserve"]
CatalogMatch = Literal["exact", "casefold", "lower", "upper"]
InformationSchemaScope = Literal["catalog", "schema"]

TIERS: tuple[str, ...] = ("verified", "native", "best_effort")
COUNT_IF_STYLES: tuple[str, ...] = ("countif", "count_if", "sum_case", "filter")
LIMIT_STYLES: tuple[str, ...] = ("limit", "top", "fetch")
UNQUOTED_CASES: tuple[str, ...] = ("upper", "lower", "preserve")
QUOTE_CHARS: tuple[str, ...] = ('"', "`", "[")
CATALOG_MATCHES: tuple[str, ...] = ("exact", "casefold", "lower", "upper")
TABLE_SAMPLE_METHODS: tuple[str, ...] = ("system", "bernoulli")
INT64 = (-(2**63), 2**63 - 1)

_SAMPLE_SLOTS = frozenset({"percent", "fraction", "seed"})


def _slots(template: str, owner: str) -> set[str]:
    try:
        parsed = list(string.Formatter().parse(template))
    except ValueError as exc:
        raise ValueError(f"{owner}: malformed template {template!r}: {exc}") from None
    return {name for _, name, _, _ in parsed if name is not None}


def _no_semicolon(text: str, owner: str) -> None:
    if ";" in text:
        raise ValueError(f"{owner} must not contain ';' (rendered SQL is a single SELECT)")


def _frozen_mapping(obj: object, name: str) -> Mapping[Any, Any]:
    value = getattr(obj, name)
    if not isinstance(value, Mapping):
        raise TypeError(f"DialectProfile.{name} must be a mapping, got {value!r}")
    frozen = MappingProxyType(dict(value))
    object.__setattr__(obj, name, frozen)
    return frozen


@dataclass(frozen=True, eq=False)
class DialectProfile:
    """Everything the native printer needs to know about one SQL dialect (spec 8.3)."""

    name: str
    tier: str
    quote_char: str
    unquoted_case: UnquotedCase
    reserved_words: frozenset[str]
    max_table_parts: int
    max_identifier_length: int
    count_if_style: CountIfStyle
    limit_style: LimitStyle
    cast_types: Mapping[str, str]
    now_sql: str
    type_families: tuple[tuple[str, str], ...]
    param_types: Mapping[str, str]
    information_schema_sql: str
    # how CatalogColumns compares a quoted / unquoted name with the catalog (spec 8.4 rule)
    quoted_catalog_match: CatalogMatch
    unquoted_catalog_match: CatalogMatch
    overrides: Mapping[type, Callable[..., str]] = field(default_factory=dict)
    quoted_column_match: CatalogMatch | None = None
    unquoted_column_match: CatalogMatch | None = None
    qualified_information_schema_sql: str | None = None
    information_schema_scope: InformationSchemaScope = "catalog"
    quote_escapes: Mapping[str, str] | None = None
    # Amendment B (spec 19 B2)
    sample_styles: Mapping[str, str | None] = field(default_factory=dict)
    keyhash_expr: str | None = None
    sample_seed_range: tuple[int, int] = INT64
    # spelling details
    string_concat: str = "||"
    cast_params: bool = False
    default_schema_sql: str | None = None
    default_catalog_sql: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not re.fullmatch(r"[a-z][a-z0-9_]*", self.name):
            raise ValueError(f"DialectProfile.name {self.name!r} must be a lowercase identifier")
        if self.tier not in TIERS:
            raise ValueError(f"DialectProfile.tier must be one of {TIERS}, got {self.tier!r}")
        if self.quote_char not in QUOTE_CHARS:
            raise ValueError(f"DialectProfile.quote_char must be one of {QUOTE_CHARS}")
        if self.unquoted_case not in UNQUOTED_CASES:
            raise ValueError(f"DialectProfile.unquoted_case must be one of {UNQUOTED_CASES}")
        if self.count_if_style not in COUNT_IF_STYLES:
            raise ValueError(f"DialectProfile.count_if_style must be one of {COUNT_IF_STYLES}")
        if self.limit_style not in LIMIT_STYLES:
            raise ValueError(f"DialectProfile.limit_style must be one of {LIMIT_STYLES}")
        for name in ("max_table_parts", "max_identifier_length"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int) or v < 1:
                raise ValueError(f"DialectProfile.{name} must be a positive int, got {v!r}")
        if self.max_table_parts > 3:
            raise ValueError("DialectProfile.max_table_parts is at most 3 (catalog.schema.table)")
        object.__setattr__(
            self, "reserved_words", frozenset(w.upper() for w in self.reserved_words)
        )
        casts = _frozen_mapping(self, "cast_types")
        bad = sorted(set(casts) - set(TYPE_FAMILIES))
        if bad:
            raise ValueError(f"DialectProfile.cast_types has unknown type families {bad}")
        params = _frozen_mapping(self, "param_types")
        bad = sorted(set(params) - set(PARAM_FAMILIES))
        if bad:
            raise ValueError(f"DialectProfile.param_types has unknown parameter families {bad}")
        families = tuple(tuple(x) for x in self.type_families)
        for item in families:
            if len(item) != 2 or item[1] not in TYPE_FAMILIES:
                raise ValueError(f"DialectProfile.type_families entry {item!r} is invalid")
            try:
                re.compile(item[0])
            except re.error as exc:
                raise ValueError(f"DialectProfile.type_families regex {item[0]!r}: {exc}") from None
        object.__setattr__(self, "type_families", families)
        _frozen_mapping(self, "overrides")
        styles = _frozen_mapping(self, "sample_styles")
        for method, style in styles.items():
            if method not in TABLE_SAMPLE_METHODS:
                raise ValueError(
                    f"DialectProfile.sample_styles key {method!r} must be one of "
                    f"{TABLE_SAMPLE_METHODS} (keyhash is keyhash_expr)"
                )
            if style is not None:
                _no_semicolon(style, "DialectProfile.sample_styles")
                extra = _slots(style, "sample_styles") - _SAMPLE_SLOTS
                if extra:
                    raise ValueError(f"DialectProfile.sample_styles has unknown slots {extra}")
        if self.keyhash_expr is not None:
            _no_semicolon(self.keyhash_expr, "DialectProfile.keyhash_expr")
            if _slots(self.keyhash_expr, "keyhash_expr") != {"s"}:
                raise ValueError("DialectProfile.keyhash_expr must have exactly the {s} slot")
        if _slots(self.information_schema_sql, "information_schema_sql") != {"where"}:
            raise ValueError("DialectProfile.information_schema_sql must have exactly {where}")
        for name in (
            "information_schema_sql",
            "now_sql",
            "string_concat",
            "default_schema_sql",
            "default_catalog_sql",
        ):
            value = getattr(self, name)
            if value is not None:
                _no_semicolon(value, f"DialectProfile.{name}")
        for name in ("quoted_catalog_match", "unquoted_catalog_match"):
            if getattr(self, name) not in CATALOG_MATCHES:
                raise ValueError(f"DialectProfile.{name} must be one of {CATALOG_MATCHES}")
        for name in ("quoted_column_match", "unquoted_column_match"):
            value = getattr(self, name)
            if value is not None and value not in CATALOG_MATCHES:
                raise ValueError(f"DialectProfile.{name} must be None or one of {CATALOG_MATCHES}")
        if self.information_schema_scope not in ("catalog", "schema"):
            raise ValueError("DialectProfile.information_schema_scope must be catalog or schema")
        qualified = self.qualified_information_schema_sql
        if qualified is not None:
            _no_semicolon(qualified, "DialectProfile.qualified_information_schema_sql")
            if _slots(qualified, "qualified_information_schema_sql") != {"where", "prefix"}:
                raise ValueError(
                    "DialectProfile.qualified_information_schema_sql must have exactly "
                    "{prefix} and {where}"
                )
        if self.quote_escapes is not None:
            escapes = _frozen_mapping(self, "quote_escapes")
            close = "]" if self.quote_char == "[" else self.quote_char
            if close not in escapes or any(
                not isinstance(k, str) or len(k) != 1 or not isinstance(v, str)
                for k, v in escapes.items()
            ):
                raise ValueError(
                    "DialectProfile.quote_escapes maps single characters to text and must "
                    "cover the closing quote character"
                )
        if self.cast_params:
            missing = sorted(set(PARAM_FAMILIES) - set(params))
            if missing:
                raise ValueError(
                    f"DialectProfile.param_types must cover every parameter family when "
                    f"cast_params is set; missing {missing}"
                )
        lo, hi = self.sample_seed_range
        if lo > hi:
            raise ValueError(
                "DialectProfile.sample_seed_range must be (low, high) with low <= high"
            )

    # -- small decisions ----------------------------------------------------------------

    @property
    def supports_filter_clause(self) -> bool:
        return self.count_if_style == "filter"

    def fold(self, text: str) -> str:
        """What an unquoted identifier resolves to under this dialect's case rule."""
        if self.unquoted_case == "upper":
            return text.upper()
        if self.unquoted_case == "lower":
            return text.lower()
        return text

    def quote_ident(self, text: str) -> str:
        """``text`` quoted with this dialect's quote character: the closing one doubled, or
        every character spelled per ``quote_escapes`` when the profile declares them."""
        close = "]" if self.quote_char == "[" else self.quote_char
        if self.quote_escapes is not None:
            escapes = self.quote_escapes
            return self.quote_char + "".join(escapes.get(ch, ch) for ch in text) + close
        return self.quote_char + text.replace(close, close + close) + close

    def column_match(self, quoted: bool) -> str:
        """How CatalogColumns compares a column name (falls back to the table rule)."""
        mode = self.quoted_column_match if quoted else self.unquoted_column_match
        if mode is not None:
            return mode
        return self.quoted_catalog_match if quoted else self.unquoted_catalog_match

    def is_reserved(self, text: str) -> bool:
        return text.upper() in self.reserved_words

    def supports_sample(self, method: str) -> bool:
        if method == "keyhash":
            return self.keyhash_expr is not None
        return bool(self.sample_styles.get(method))


_WRAPPERS = re.compile(r"(?:Nullable|LowCardinality)\((.*)\)", re.IGNORECASE | re.DOTALL)
_SCALE_ZERO = re.compile(
    r"(?:decimal|numeric|number|dec)\s*\(\s*\d+\s*(?:,\s*0+\s*)?\)", re.IGNORECASE
)


def family_of(native_type: str, profile: DialectProfile) -> str:
    """The FactLang type family of a warehouse type name.

    Unwraps ``Nullable(…)``/``LowCardinality(…)``, maps a decimal with scale 0 to ``integer``,
    then returns the family of the first ``profile.type_families`` regex that matches the
    whole name (case-insensitive), else ``other``.
    """
    t = native_type.strip()
    while m := _WRAPPERS.fullmatch(t):
        t = m.group(1).strip()
    if _SCALE_ZERO.fullmatch(t):
        return "integer"
    for pattern, family in profile.type_families:
        if re.fullmatch(pattern, t, re.IGNORECASE):
            return family
    return "other"


__all__ = [
    "CATALOG_MATCHES",
    "COUNT_IF_STYLES",
    "LIMIT_STYLES",
    "QUOTE_CHARS",
    "TIERS",
    "UNQUOTED_CASES",
    "CatalogMatch",
    "CountIfStyle",
    "DialectProfile",
    "InformationSchemaScope",
    "LimitStyle",
    "UnquotedCase",
    "family_of",
]
