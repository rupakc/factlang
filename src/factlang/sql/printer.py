"""Native printer: internal SQL AST + :class:`DialectProfile` -> SQL text (spec 8.3, 8.4).

Safety (spec 8.4):

- an unquoted identifier is emitted unquoted, unless it is a reserved word, then it is quoted
  after the profile's ``unquoted_case`` fold; a quoted identifier is quoted, inner quote
  characters doubled, case preserved;
- an identifier containing NUL, a newline, ``;``, ``{{`` or ``}}``, longer than
  ``max_identifier_length`` (in UTF-8 bytes), or an "unquoted" one that is not a plain
  ``[A-Za-z_][A-Za-z0-9_$]*`` name, is refused with ``FL502``, never sanitised (``;`` is
  refused because a rendered statement may hold no ``;`` at all, so a quoted ``"a;b"`` is
  FL502 rather than a guard failure);
- every user value is a ``{{name}}`` bind parameter; the only numbers written into the text
  are FactLang-validated (``TableSample`` fraction/seed, ``Select.limit``) and the constants
  of :data:`~factlang.sql.ast.LITERAL_CONSTANTS`;
- :func:`render` refuses any statement that does not start with ``SELECT``/``WITH`` or that
  contains ``;``.
"""

from __future__ import annotations

import re
from decimal import Decimal

from factlang.ir import ProbePlan
from factlang.semantics.filters import Param
from factlang.semantics.refs import Ident, TableRef
from factlang.sql import ast as A
from factlang.sql.lower import Lowerer
from factlang.sql.probe import RenderedProbe, RenderError, placeholder
from factlang.sql.profile import DialectProfile

_PLAIN = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("\x00", "NUL"),
    ("\n", "a newline"),
    ("\r", "a carriage return"),
    ("{{", "'{{'"),
    ("}}", "'}}'"),
    (";", "';'"),
)


def _percent(fraction: Decimal) -> str:
    text = format((fraction * 100).normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


class Printer:
    """Prints one statement (or fragment) and collects its parameters in first-use order."""

    def __init__(self, profile: DialectProfile) -> None:
        if not isinstance(profile, DialectProfile):
            raise TypeError(f"profile must be a DialectProfile, got {profile!r}")
        self.profile = profile
        self._params: dict[str, Param] = {}
        self._reserved: set[str] = set()
        self._catalog_count = 0
        self._derived = 0

    @property
    def params(self) -> tuple[Param, ...]:
        return tuple(self._params.values())

    # -- public entry points --------------------------------------------------------------

    def statement(self, node: A.Select | A.UnionAll) -> str:
        """A full statement; a top-level UNION ALL is wrapped so it starts with SELECT.

        The profile's ``statement_suffix`` is appended.
        """
        self._reserve(node)
        if isinstance(node, A.UnionAll):
            text = f"SELECT * FROM ({self.union(node)}) {self.derived_alias()}"
        else:
            text = self.select(node)
        return text + self.profile.statement_suffix

    def expression(self, node: A.Expr) -> str:
        self._reserve(node)
        return self.expr(node)

    def from_clause(self, node: A.FromItem) -> str:
        self._reserve(node)
        return self.from_item(node)

    # -- identifiers ------------------------------------------------------------------------

    def check_ident(self, text: str, quoted: bool) -> None:
        p = self.profile
        for bad, what in _FORBIDDEN:
            if bad in text:
                raise RenderError(f"FL502 identifier {text!r} contains {what}")
        if len(text.encode("utf-8")) > p.max_identifier_length:
            raise RenderError(
                f"FL502 identifier {text[:40]!r}… is longer than {p.max_identifier_length} "
                f"bytes, the {p.name} limit"
            )
        if not text:
            raise RenderError("FL502 empty identifier")
        if not quoted and not _PLAIN.fullmatch(text):
            raise RenderError(f"FL502 unquoted identifier {text!r} is not a plain name")

    def ident(self, i: Ident) -> str:
        if not isinstance(i, Ident):
            raise TypeError(f"expected an Ident, got {i!r}")
        self.check_ident(i.text, i.quoted)
        p = self.profile
        if i.quoted:
            return p.quote_ident(i.text)
        if p.is_reserved(i.text):
            return p.quote_ident(p.fold(i.text))
        return i.text

    def alias(self, name: str) -> str:
        """A FactLang-chosen (or caller-given) alias: exact text, quoted only if reserved."""
        self.check_ident(name, False)
        return self.profile.quote_ident(name) if self.profile.is_reserved(name) else name

    def table(self, ref: TableRef) -> str:
        parts = ref.parts()
        if len(parts) > self.profile.max_table_parts:
            raise RenderError(
                f"FL505 table {'.'.join(x.text for x in parts)!r} has {len(parts)} name parts; "
                f"{self.profile.name} accepts at most {self.profile.max_table_parts}"
            )
        return ".".join(self.ident(x) for x in parts)

    # -- parameters -------------------------------------------------------------------------

    def _reserve(self, node: object) -> None:
        self._reserved |= {n.param.name for n in A.walk(node) if isinstance(n, A.ParamRef)}

    def param(self, p: Param) -> str:
        prior = self._params.setdefault(p.name, p)
        if prior != p:
            raise RenderError(f"FL500 parameter name {p.name!r} is bound to two different values")
        text = placeholder(p.name)
        if self.profile.cast_params:
            return f"CAST({text} AS {self.profile.param_types[p.family]})"
        return text

    def catalog_param(self, value: str) -> str:
        while True:
            self._catalog_count += 1
            name = f"cat{self._catalog_count}"
            if name not in self._reserved and name not in self._params:
                break
        return self.param(Param(name, value, "string"))

    def derived_alias(self) -> str:
        self._derived += 1
        return f"fl_d{self._derived}"

    # -- expressions ------------------------------------------------------------------------

    def expr(self, x: object) -> str:
        override = self.profile.overrides.get(type(x))
        if override is not None:
            return override(self, x)
        p = self.profile
        if isinstance(x, A.Column):
            col = self.ident(x.name)
            return f"{self.alias(x.qualifier)}.{col}" if x.qualifier else col
        if isinstance(x, A.ParamRef):
            return self.param(x.param)
        if isinstance(x, A.Literal):
            if x.value is None:
                return "NULL"
            return f"'{x.value}'" if isinstance(x.value, str) else str(x.value)
        if isinstance(x, A.Star):
            return "*"
        if isinstance(x, A.Agg):
            arg = "*" if x.arg is None else self.expr(x.arg)
            return f"{x.fn}({'DISTINCT ' if x.distinct else ''}{arg})"
        if isinstance(x, A.CountIf):
            return self.count_if(x.cond)
        if isinstance(x, A.Lower):
            return f"LOWER({self.expr(x.e)})"
        if isinstance(x, A.Trim):
            return f"TRIM({self.expr(x.e)})"
        if isinstance(x, A.SplitPrefix):
            return f"SPLIT_PART({self.expr(x.e)}, {self.expr(x.separator)}, 1)"
        if isinstance(x, A.Like):
            return f"{self.expr(x.e)} LIKE {self.expr(x.pattern)}"
        if isinstance(x, A.Cast):
            return f"CAST({self.expr(x.e)} AS {self.cast_type(x.family)})"
        if isinstance(x, A.Coalesce):
            return "COALESCE(" + ", ".join(self.expr(i) for i in x.items) + ")"
        if isinstance(x, A.Case):
            whens = " ".join(f"WHEN {self.expr(c)} THEN {self.expr(r)}" for c, r in x.whens)
            tail = f" ELSE {self.expr(x.else_)}" if x.else_ is not None else ""
            return f"CASE {whens}{tail} END"
        if isinstance(x, A.Now):
            return p.now_sql
        if isinstance(x, A.Compare):
            op = "<>" if x.op == "!=" else x.op
            return f"{self.expr(x.left)} {op} {self.expr(x.right)}"
        if isinstance(x, (A.And, A.Or)):
            word = " AND " if isinstance(x, A.And) else " OR "
            return "(" + word.join(self.expr(i) for i in x.items) + ")"
        if isinstance(x, A.Not):
            return f"NOT ({self.expr(x.e)})"
        if isinstance(x, A.InList):
            items = ", ".join(self.expr(i) for i in x.items)
            return f"{self.expr(x.e)} {'NOT ' if x.negated else ''}IN ({items})"
        if isinstance(x, A.IsNull):
            return f"{self.expr(x.e)} IS {'NOT ' if x.negated else ''}NULL"
        if isinstance(x, A.Between):
            return f"{self.expr(x.e)} BETWEEN {self.expr(x.lo)} AND {self.expr(x.hi)}"
        if isinstance(x, A.KeyHash):
            return self.keyhash(x)
        raise RenderError(f"FL500 cannot print {type(x).__name__} as an expression")

    def cast_type(self, family: str) -> str:
        native = self.profile.cast_types.get(family)
        if native is None:
            raise RenderError(f"FL503 cast to {family} is not expressible in {self.profile.name}")
        return native

    def count_if(self, cond: A.Expr) -> str:
        """Rows where ``cond`` is TRUE; ``0`` (never NULL) over no rows."""
        c = self.expr(cond)
        style = self.profile.count_if_style
        if style == "filter":
            return f"COUNT(*) FILTER (WHERE {c})"
        if style == "countif":
            return f"COALESCE(COUNTIF({c}), 0)"
        if style == "count_if":
            return f"COALESCE(COUNT_IF({c}), 0)"
        return f"COALESCE(SUM(CASE WHEN {c} THEN 1 ELSE 0 END), 0)"

    def keyhash(self, x: A.KeyHash) -> str:
        template = self.profile.keyhash_expr
        if template is None:
            raise RenderError(
                f"FL506 sample method keyhash is not supported by dialect {self.profile.name}"
            )
        cc = self.profile.string_concat
        parts = [self.expr(x.seed), *(self.expr(part) for part in x.parts)]
        s = "(" + f" {cc} '|' {cc} ".join(parts) + ")"
        return template.format(s=s)

    # -- relations --------------------------------------------------------------------------

    def from_item(self, x: object) -> str:
        override = self.profile.overrides.get(type(x))
        if override is not None:
            return override(self, x)
        if isinstance(x, A.TableName):
            out = self.table(x.ref)
            if x.alias is not None:
                out += " " + self.alias(x.alias)
            if x.sample is not None:
                out += " " + self.table_sample(x.sample)
            return out
        if isinstance(x, A.SubqueryFrom):
            inner = (
                self.union(x.select) if isinstance(x.select, A.UnionAll) else self.select(x.select)
            )
            return f"({inner}) {self.alias(x.alias)}"
        if isinstance(x, A.Join):
            left, right = self.from_item(x.left), self.from_item(x.right)
            if x.on is None:
                return f"{left} CROSS JOIN {right}"
            return f"{left} {x.kind} JOIN {right} ON {self.expr(x.on)}"
        if isinstance(x, A.CatalogColumns):
            return f"({self.catalog_columns(x)}) {self.derived_alias()}"
        raise RenderError(f"FL500 cannot print {type(x).__name__} as a FROM item")

    def table_sample(self, s: A.TableSample) -> str:
        p = self.profile
        style = p.sample_styles.get(s.method)
        if not style:
            raise RenderError(
                f"FL506 sample method {s.method} is not supported by dialect {p.name}"
            )
        lo, hi = p.sample_seed_range
        if not lo <= s.seed <= hi:
            raise RenderError(
                f"FL506 sample seed {s.seed} is outside {p.name}'s range [{lo}, {hi}]"
            )
        return style.format(
            percent=_percent(s.fraction), fraction=format(s.fraction, "f"), seed=s.seed
        )

    def catalog_columns(self, x: A.CatalogColumns) -> str:
        """The profile's information_schema query; names are bound as string parameters."""
        p = self.profile
        ref = x.table
        if len(ref.parts()) > p.max_table_parts:
            self.table(ref)  # raises FL505
        conds: list[str] = []

        def match(column: str, name: Ident) -> None:
            self.check_ident(name.text, name.quoted)
            if column == "column_name":
                mode = p.column_match(name.quoted)
            else:
                mode = p.quoted_catalog_match if name.quoted else p.unquoted_catalog_match
            if mode == "casefold":
                conds.append(f"LOWER({column}) = LOWER({self.catalog_param(name.text)})")
                return
            value = {"lower": name.text.lower(), "upper": name.text.upper()}.get(mode, name.text)
            conds.append(f"{column} = {self.catalog_param(value)}")

        match("table_name", ref.table)
        if ref.schema is not None:
            match("table_schema", ref.schema)
        elif p.default_schema_sql is not None:
            conds.append(f"table_schema = {p.default_schema_sql}")
        if ref.catalog is not None:
            match("table_catalog", ref.catalog)
        elif p.default_catalog_sql is not None:
            conds.append(f"table_catalog = {p.default_catalog_sql}")
        if x.column is not None:
            match("column_name", x.column)
        where = " AND ".join(conds)
        qualified = p.qualified_information_schema_sql
        # the parts that own the catalog view: the catalog, or [catalog.]schema (BigQuery)
        owner: tuple[Ident, ...]
        if p.information_schema_scope == "catalog":
            owner = (ref.catalog,) if ref.catalog is not None else ()
        else:
            owner = ref.parts()[:-1]
        if qualified is not None and owner:
            prefix = "".join(self.ident(i) + "." for i in owner)
            return qualified.format(prefix=prefix, where=where)
        return p.information_schema_sql.format(where=where)

    # -- statements -------------------------------------------------------------------------

    def select(self, x: A.Select) -> str:
        p = self.profile
        top = f"TOP {x.limit} " if x.limit is not None and p.limit_style == "top" else ""
        cols = ", ".join(f"{self.expr(c.expr)} AS {self.alias(c.alias)}" for c in x.columns)
        out = f"SELECT {top}{cols} FROM {self.from_item(x.from_)}"
        if x.where is not None:
            out += " WHERE " + self.expr(x.where)
        if x.group_by:
            out += " GROUP BY " + ", ".join(self.expr(g) for g in x.group_by)
        if x.order_by:
            out += " ORDER BY " + ", ".join(
                self.expr(o.expr) + (" DESC" if o.descending else " ASC") for o in x.order_by
            )
        if x.limit is not None:
            if p.limit_style == "limit":
                out += f" LIMIT {x.limit}"
            elif p.limit_style == "fetch":
                out += f" FETCH FIRST {x.limit} ROWS ONLY"
        return out

    def union(self, x: A.UnionAll) -> str:
        """Branches with ORDER BY/LIMIT are parenthesised (a derived table under ``TOP``)."""
        branches = []
        for s in x.selects:
            text = self.select(s)
            if s.order_by or s.limit is not None:
                if self.profile.limit_style == "top":
                    text = f"SELECT * FROM ({text}) {self.derived_alias()}"
                else:
                    text = f"({text})"
            branches.append(text)
        return " UNION ALL ".join(branches)


def guard(sql: str) -> None:
    """Spec 8.4: a rendered probe is one SELECT: starts with SELECT/WITH, no ``;`` at all."""
    if not sql.startswith(("SELECT ", "WITH ")) or ";" in sql:
        raise RenderError(f"FL500 rendered SQL failed the SELECT-only guard: {sql[:80]!r}")


def print_select(
    node: A.Select | A.UnionAll, profile: DialectProfile
) -> tuple[str, tuple[Param, ...]]:
    """Print a statement tree; returns the ``{{name}}`` template and its params in use order."""
    A.params_of(node)  # FL500 when one parameter name maps to two different values
    printer = Printer(profile)
    sql = printer.statement(node)
    return sql, printer.params


def render(plan: ProbePlan, profile: DialectProfile) -> RenderedProbe:
    """Lower ``plan`` and print it for ``profile``; the result passed the SELECT-only guard."""
    tree = Lowerer().lower(plan)
    sql, params = print_select(tree, profile)
    guard(sql)
    return RenderedProbe(
        sql,
        params,
        plan.arity,
        plan,
        profile.name,
        profile.tier,
    )


__all__ = ["Printer", "guard", "print_select", "render"]
