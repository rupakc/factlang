"""Analyser: parsed ``Document`` + verb registry (+ catalog) -> ``CheckedClaim``s (spec 4, 6, 12).

Per claim, in order: verb (FL300), operands and ``USE`` resolution (FL301, FL302), attributes
(FL303 unknown, FL306 operator, FL305 literal kind, FL309 invalid value), presence rules
(FL304 required / ``requires`` / a lone ``WITH`` or ``TO`` on an edge verb; FL309 ``exclusive``
pairs and unequal ``WITH``/``TO`` lengths), column lists (FL307), the filter (FL308, typed
parameters ``p1, p2, ...`` in source order, FL309 invalid literals) and, when a catalog is
given, names (FL311 on a definite ``False`` only; FL313 info once per claim when any answer
was unknown; FL312 warning for a cross-family edge without ``CAST``; FL305 for a bare boolean
filter atom on a column the catalog knows is not boolean).

A claim with any error yields diagnostics and no ``CheckedClaim``; warnings and infos do not
drop it. ``analyze`` never raises: exceptions from host code (custom value types, catalogs)
are turned into diagnostics or treated as "unknown".
"""

from __future__ import annotations

import difflib
import re
from dataclasses import replace
from decimal import Decimal
from typing import TypeVar

from factlang.diagnostics import DiagnosticBag, Span
from factlang.semantics.catalog import Catalog
from factlang.semantics.checked import CheckedAttr, CheckedClaim
from factlang.semantics.filters import (
    FAnd,
    FBetween,
    FBool,
    FCompare,
    FilterExpr,
    FIn,
    FIsNull,
    FNot,
    FOr,
    Param,
)
from factlang.semantics.identity import semantic_hash, value_text
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef
from factlang.semantics.signature import OperandKind, OperandSpec, VerbRegistry, VerbSpec
from factlang.semantics.types import MAX_EXPONENT, Invalid, ValueType, get_type
from factlang.syntax import ast
from factlang.syntax.formatter import format_claim

LK = ast.LiteralKind
Operand = TableRef | ColumnRef | EdgeRef
_T = TypeVar("_T")

# Edge verbs: `WITH [...]` lists further left-table key columns, `TO [...]` the matching
# right-table ones, positionally (spec 6.3, Amendment A R3).
_LEFT_LIST, _RIGHT_LIST = "WITH", "TO"
_CAST_ATTR = "CAST"
_MAX_TABLE_PARTS = 3


def analyze(
    document: ast.Document,
    registry: VerbRegistry,
    *,
    catalog: Catalog | None = None,
    bag: DiagnosticBag,
) -> list[CheckedClaim]:
    """Check every claim of ``document``; diagnostics go to ``bag``. Never raises."""
    out: list[CheckedClaim] = []
    namespace: tuple[Ident | None, Ident | None] = (None, None)
    for item in document.items:
        if isinstance(item, ast.Directive):
            namespace = _directive(item, namespace, bag)
            continue
        checked = _ClaimCheck(item, registry, catalog, namespace, bag).run()
        if checked is not None:
            out.append(checked)
    return out


def _ident(name: ast.Name) -> Ident:
    return Ident(name.text, name.quoted)


def _directive(
    d: ast.Directive, current: tuple[Ident | None, Ident | None], bag: DiagnosticBag
) -> tuple[Ident | None, Ident | None]:
    parts = [_ident(p) for p in d.namespace.parts]
    if len(parts) == 1:
        return (None, parts[0])
    if len(parts) == 2:
        return (parts[0], parts[1])
    bag.error(
        "FL302",
        f"USE takes [catalog.]schema (1 or 2 parts), got {len(parts)} parts",
        d.namespace.span,
        hint="the directive is ignored; the previous namespace stays in effect",
    )
    return current


def _exists(catalog: Catalog, ref: TableRef) -> bool | None:
    """``table_exists`` with anything but ``True``/``False`` (or an exception) as unknown."""
    try:
        answer: object = catalog.table_exists(ref)
    except Exception:  # a failing catalog knows nothing
        return None
    return answer if isinstance(answer, bool) else None


def _columns(catalog: Catalog, ref: TableRef) -> dict[str, str] | None:
    """``columns`` validated to ``dict[str, str]``; any other shape (or an exception) is unknown."""
    try:
        answer: object = catalog.columns(ref)
    except Exception:
        return None
    if not isinstance(answer, dict):
        return None
    out: dict[str, str] = {}
    for name, typ in answer.items():
        if not isinstance(name, str) or not isinstance(typ, str):
            return None
        out[name] = typ
    return out


def _unquoted(ref: TableRef) -> TableRef:
    def u(i: Ident | None) -> Ident | None:
        return None if i is None else Ident(i.text)

    return TableRef(u(ref.catalog), u(ref.schema), Ident(ref.table.text))


# --- native type -> family (best effort; None = unknown, never a diagnostic) ----------

_WRAPPER = re.compile(r"^(?:nullable|lowcardinality)\s*\((.*)\)$", re.S)
_DECIMALS = r"(?:numeric|decimal|number|bignumeric|bigdecimal|dec)"
_FAMILY_RULES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(p), fam)
    for p, fam in (
        (r"bool|boolean", "boolean"),
        (_DECIMALS + r"\s*\(\s*\d+\s*,\s*0\s*\)", "integer"),
        (_DECIMALS + r"(\s*\(.*\))?", "decimal"),
        (
            r"(tinyint|smallint|mediumint|int|integer|bigint|hugeint|long|short|byte|"
            r"u?int\d*|u?tinyint|u?smallint|u?bigint|u?hugeint|u?integer|"
            r"serial|smallserial|bigserial|serial\d)(\s*\(\s*\d+\s*\))?(\s+unsigned)?",
            "integer",
        ),
        (r"(float|float\d+|double|double precision|real)(\s*\(\s*\d+\s*\))?", "float"),
        (
            r"(varchar|char|character|character varying|text|string|nvarchar|nchar|ntext|"
            r"clob|varchar2|nvarchar2|fixedstring|bpchar|tinytext|mediumtext|longtext|"
            r"citext)(\s*\(.*\))?",
            "string",
        ),
        (r"date|date32", "date"),
        (r"(timestamp|datetime).*", "timestamp"),
        (r"time.*", "time"),
        (r"(bytes|bytea|binary|varbinary|blob|longblob|mediumblob|tinyblob)(\s*\(.*\))?", "bytes"),
        (r"json|jsonb|variant", "json"),
        (r"(array|list)\b.*|.*\[\]", "array"),
        (r"(struct|record|map|object|tuple|row)\b.*", "struct"),
    )
)


# Families whose values can never be a boolean. Integer-like types (``tinyint(1)``, ``UInt8``,
# ``NUMBER(1,0)``) often hold flags and, like unrecognised types, count as unknown.
_NEVER_BOOLEAN = frozenset(
    {"string", "date", "timestamp", "time", "json", "array", "struct", "bytes", "decimal", "float"}
)


def type_family(native: str) -> str | None:
    """Family of a catalog type name (spec 6.1 families), or ``None`` when unrecognised."""
    text = native.strip().lower()
    while True:
        m = _WRAPPER.fullmatch(text)
        if m is None:
            break
        text = m.group(1).strip()
    for pattern, family in _FAMILY_RULES:
        if pattern.fullmatch(text):
            return family
    return None


def _lookup(cols: dict[str, str], col: Ident) -> str | None:
    """Type of ``col`` in ``cols``: exact name first, then any case variant.

    The analyser knows no dialect, so quoted and bare names alike match case-insensitively;
    a column is absent (FL311) only when no case variant exists.
    """
    if col.text in cols:
        return cols[col.text]
    lower = col.text.lower()
    for name, typ in cols.items():
        if name.lower() == lower:
            return typ
    return None


def _show(t: TableRef) -> str:
    return ".".join(f'"{p.text}"' if p.quoted else p.text for p in t.parts())


class _ClaimCheck:
    def __init__(
        self,
        claim: ast.Claim,
        registry: VerbRegistry,
        catalog: Catalog | None,
        namespace: tuple[Ident | None, Ident | None],
        bag: DiagnosticBag,
    ) -> None:
        self.claim = claim
        self.registry = registry
        self.catalog = catalog
        self.namespace = namespace
        self.bag = bag
        self.ok = True
        self.params = 0
        self.filter_cols: list[tuple[Ident, Span]] = []
        self.bool_cols: list[tuple[Ident, Span]] = []

    def error(self, code: str, message: str, span: Span | None, **kw: str | None) -> None:
        self.ok = False
        self.bag.error(code, message, span, **kw)

    # -- driver ------------------------------------------------------------------------

    def run(self) -> CheckedClaim | None:
        c = self.claim
        spec = self.registry.get(c.verb)
        if spec is None:
            self.error(
                "FL300",
                f"unknown verb {c.verb}",
                c.verb_span,
                suggestion=self.registry.suggest(c.verb),
            )
            return None
        operands = self.operands(spec)
        attrs = self.attributes(spec)
        self.presence(spec, attrs)
        if operands is not None:
            self.column_lists(spec, operands, attrs)
        flt = self.filter(spec)
        if operands is not None and self.catalog is not None:
            self.catalog_checks(operands, attrs)
        if not self.ok or operands is None:
            return None
        return CheckedClaim(
            verb=c.verb,
            operands=operands,
            attrs=attrs,
            filter=flt,
            canonical_text=self.canonical_text(operands),
            semantic_hash=semantic_hash(c.verb, operands, attrs, flt),
            span=c.span,
            source_text=c.line_text,
            comment=c.comment,
        )

    # -- operands ----------------------------------------------------------------------

    def operands(self, spec: VerbSpec) -> tuple[Operand, ...] | None:
        c = self.claim
        want, got = len(spec.operands), len(c.operands)
        if want != got:
            names = ", ".join(f"{o.name} ({o.kind.value.lower()})" for o in spec.operands)
            span = c.operands[want].span if got > want else c.verb_span
            self.error(
                "FL301",
                f"{c.verb} takes {want} operand{'s' if want != 1 else ''} ({names}), got {got}",
                span,
            )
            return None
        out: list[Operand] = []
        for node, ospec in zip(c.operands, spec.operands, strict=True):
            r = self.operand(node, ospec)
            if r is not None:
                out.append(r)
        return tuple(out) if len(out) == want else None

    def operand(self, node: ast.Ref | ast.Edge | ast.Literal, ospec: OperandSpec) -> Operand | None:
        kind = ospec.kind
        if kind is OperandKind.TABLE and isinstance(node, ast.Ref):
            if len(node.parts) <= _MAX_TABLE_PARTS:
                return self.table(node.parts)
            self.error(
                "FL302",
                f"{ospec.name}: a table reference has at most {_MAX_TABLE_PARTS} parts "
                f"([catalog.][schema.]table), got {len(node.parts)}",
                node.span,
            )
            return None
        if kind is OperandKind.COLUMN and isinstance(node, ast.Ref):
            return self.column(node, ospec.name)
        if kind is OperandKind.EDGE and isinstance(node, ast.Edge):
            left = self.column(node.left, ospec.name + " (left side)")
            right = self.column(node.right, ospec.name + " (right side)")
            return EdgeRef(left, right) if left is not None and right is not None else None
        what = {
            OperandKind.TABLE: "a table reference",
            OperandKind.COLUMN: "a column reference (table.column)",
            OperandKind.EDGE: "an edge (table.column -> table.column)",
        }[kind]
        found = (
            "an edge"
            if isinstance(node, ast.Edge)
            else "a literal"
            if isinstance(node, ast.Literal)
            else "a reference"
        )
        self.error("FL302", f"{ospec.name}: expected {what}, found {found}", node.span)
        return None

    def column(self, ref: ast.Ref, what: str) -> ColumnRef | None:
        n = len(ref.parts)
        if 2 <= n <= _MAX_TABLE_PARTS + 1:
            return ColumnRef(self.table(ref.parts[:-1]), _ident(ref.parts[-1]))
        need = "a table and a column (table.column)" if n < 2 else "at most 4 parts"
        self.error(
            "FL302",
            f"{what}: a column reference needs {need}, got {n} part{'s' if n != 1 else ''}",
            ref.span,
            hint="composite keys use WITH [col, ...]" if n > 2 else None,
        )
        return None

    def table(self, parts: tuple[ast.Name, ...]) -> TableRef:
        names = [_ident(p) for p in parts]
        pad = _MAX_TABLE_PARTS - len(names)
        slots: list[Ident | None] = [*list(self.namespace)[:pad], *names]
        return TableRef(slots[0], slots[1], names[-1])

    # -- attributes --------------------------------------------------------------------

    def attributes(self, spec: VerbSpec) -> dict[str, CheckedAttr]:
        specs = {a.name: a for a in spec.attrs}
        out: dict[str, CheckedAttr] = {}
        for a in self.claim.attributes:
            aspec = specs.get(a.name)
            if aspec is None:
                close = difflib.get_close_matches(a.name, list(specs), n=1, cutoff=0.6)
                known = ", ".join(specs) or "none"
                self.error(
                    "FL303",
                    f"{spec.name} has no attribute {a.name}",
                    a.span,
                    hint=f"attributes of {spec.name}: {known}",
                    suggestion=close[0] if close else None,
                )
                continue
            op = "==" if a.op == "=" else a.op
            allowed = tuple(dict.fromkeys("==" if o == "=" else o for o in aspec.ops))
            if not allowed and op is not None:
                self.error("FL306", f"{a.name} takes no operator, found {a.op}", a.span)
            elif allowed and op is None:
                self.error(
                    "FL306", f"{a.name} needs an operator: one of {' '.join(allowed)}", a.span
                )
            elif op is not None and op not in allowed:
                self.error(
                    "FL306",
                    f"operator {a.op} is not allowed for {a.name}; use one of {' '.join(allowed)}",
                    a.span,
                )
            try:
                vt = get_type(aspec.type)
            except KeyError:
                self.error("FL305", f"{a.name}: unknown value type {aspec.type!r}", a.span)
                continue
            if a.value.kind not in vt.accepts:
                kinds = ", ".join(sorted(k.value.lower() for k in vt.accepts))
                self.error(
                    "FL305",
                    f"{a.name} expects {aspec.type.lower()} ({kinds}), "
                    f"found {a.value.kind.value.lower()}",
                    a.value.span,
                )
                continue
            value = self.coerce(vt, a)
            if value is None:
                continue
            try:
                value_text(value)
            except Exception:  # unsupported or non-finite: no canonical form
                self.error(
                    "FL305",
                    f"{a.name}: value type {aspec.type} produced a "
                    f"{type(value).__name__}, which has no canonical form",
                    a.value.span,
                )
                continue
            out[a.name] = CheckedAttr(op, value, aspec.type)
        return out

    def coerce(self, vt: ValueType, a: ast.Attribute) -> object | None:
        try:
            value = vt.coerce(a.value) if vt.coerce is not None else a.value.value
            msg = vt.validate(value) if vt.validate is not None else None
        except Exception as exc:  # host-supplied value types must not crash analysis
            value, msg = None, f"value could not be checked: {exc}"
        if msg is None and isinstance(value, Invalid):
            msg = value.message
        if msg is not None:
            self.error("FL309", f"{a.name}: {msg}", a.value.span)
            return None
        return value

    def presence(self, spec: VerbSpec, attrs: dict[str, CheckedAttr]) -> None:
        c = self.claim
        written = {a.name: a for a in c.attributes}
        declared = {a.name for a in spec.attrs}
        missing: set[str] = set()

        def need(name: str, why: str) -> None:
            if name not in missing:
                missing.add(name)
                self.error("FL304", why, c.verb_span)

        for aspec in spec.attrs:
            if aspec.required and aspec.name not in written:
                need(aspec.name, f"{spec.name} requires attribute {aspec.name}")
        for first, second in spec.requires:
            if first in written and second not in written:
                need(second, f"{first} requires {second}")
        has_edge = any(o.kind is OperandKind.EDGE for o in spec.operands)
        if has_edge and {_LEFT_LIST, _RIGHT_LIST} <= declared:
            lw, rw = _LEFT_LIST in written, _RIGHT_LIST in written
            if lw != rw:
                gone = _RIGHT_LIST if lw else _LEFT_LIST
                need(gone, f"{_LEFT_LIST} and {_RIGHT_LIST} must appear together: missing {gone}")
            left, right = attrs.get(_LEFT_LIST), attrs.get(_RIGHT_LIST)
            if left is not None and right is not None:
                lv, rv = left.value, right.value
                if isinstance(lv, tuple) and isinstance(rv, tuple) and len(lv) != len(rv):
                    self.error(
                        "FL309",
                        f"{_LEFT_LIST} lists {len(lv)} column(s) but {_RIGHT_LIST} lists "
                        f"{len(rv)}; they pair positionally and must have equal length",
                        written[_RIGHT_LIST].span,
                    )
        exclusive: tuple[tuple[str, str], ...] = getattr(spec, "exclusive", ())
        for first, second in exclusive:
            if first in written and second in written:
                self.error(
                    "FL309",
                    f"{first} and {second} are mutually exclusive",
                    written[second].span,
                )

    def column_lists(
        self, spec: VerbSpec, operands: tuple[Operand, ...], attrs: dict[str, CheckedAttr]
    ) -> None:
        written = {a.name: a for a in self.claim.attributes}
        for name, attr in attrs.items():
            if attr.type != "COLUMN_LIST" or not isinstance(attr.value, tuple):
                continue
            key = self.key_column(name, operands)
            key_text = key.text.lower() if key is not None else None
            seen: set[str] = set()
            for col in attr.value:
                if not isinstance(col, Ident):
                    continue
                text = col.text.lower()  # no dialect known: case variants are the same column
                if text in seen:
                    self.error(
                        "FL307", f"{name} lists column {col.text} twice", written[name].value.span
                    )
                elif text == key_text:
                    self.error(
                        "FL307",
                        f"{name} repeats the key column {col.text}; list only further key columns",
                        written[name].value.span,
                    )
                seen.add(text)

    @staticmethod
    def key_column(attr: str, operands: tuple[Operand, ...]) -> Ident | None:
        for o in operands:
            if isinstance(o, ColumnRef):
                return o.column
            if isinstance(o, EdgeRef):
                return (o.right if attr == _RIGHT_LIST else o.left).column
        return None

    # -- filter ------------------------------------------------------------------------

    def filter(self, spec: VerbSpec) -> FilterExpr | None:
        f = self.claim.filter
        if f is None:
            return None
        if not spec.filterable:
            self.error("FL308", f"{spec.name} does not accept a WHERE filter", f.span)
            return None
        return self.cond(f.condition)

    def note(self, col: ast.Name) -> Ident:
        ident = _ident(col)
        self.filter_cols.append((ident, col.span))
        return ident

    def cond(self, cond: ast.Cond) -> FilterExpr | None:
        if isinstance(cond, (ast.And, ast.Or)):
            items = [self.cond(i) for i in cond.items]
            if any(i is None for i in items):
                return None
            done = tuple(i for i in items if i is not None)
            return FAnd(done) if isinstance(cond, ast.And) else FOr(done)
        if isinstance(cond, ast.Not):
            inner = self.cond(cond.item)
            return FNot(inner) if inner is not None else None
        if isinstance(cond, ast.FilterName):
            ident = self.note(cond.column)
            self.bool_cols.append((ident, cond.column.span))
            return FBool(ident)
        if isinstance(cond, ast.IsNull):
            return FIsNull(self.note(cond.column), cond.negated)
        if isinstance(cond, ast.Comparison):
            col = self.note(cond.column)
            p = self.param(cond.value)
            return FCompare(col, cond.op, p) if p is not None else None
        if isinstance(cond, ast.InList):
            col = self.note(cond.column)
            ps = [self.param(v) for v in cond.values]
            if any(p is None for p in ps):
                return None
            return FIn(col, tuple(p for p in ps if p is not None), cond.negated)
        col = self.note(cond.column)
        lo, hi = self.param(cond.low), self.param(cond.high)
        return FBetween(col, lo, hi) if lo is not None and hi is not None else None

    def param(self, lit: ast.Literal) -> Param | None:
        self.params += 1
        name = f"p{self.params}"
        if lit.kind is LK.NUMBER and isinstance(lit.value, Decimal):
            v = lit.value
            if abs(v.adjusted()) > MAX_EXPONENT:
                self.error("FL309", "number out of range", lit.span)
                return None
            if v == v.to_integral_value():
                return Param(name, int(v), "integer")
            return Param(name, v, "decimal")
        if lit.kind is LK.STRING:
            return Param(name, str(lit.value), "string")
        if lit.kind is LK.BOOL and isinstance(lit.value, bool):
            return Param(name, lit.value, "boolean")
        if lit.kind in (LK.DATE, LK.TIMESTAMP):
            coerce = get_type("TEMPORAL").coerce
            value = coerce(lit) if coerce is not None else Invalid("no temporal coercion")
            if isinstance(value, Invalid):
                self.error("FL309", value.message, lit.span)
                return None
            return Param(name, value, "date" if lit.kind is LK.DATE else "timestamp")
        self.error("FL305", f"{lit.kind.value.lower()} is not a filter literal", lit.span)
        return None

    # -- catalog -----------------------------------------------------------------------

    def catalog_checks(self, operands: tuple[Operand, ...], attrs: dict[str, CheckedAttr]) -> None:
        catalog = self.catalog
        if catalog is None:
            return
        c = self.claim
        # table -> [(column, span)], in claim order; the first table is the filter's table
        wanted: dict[TableRef, list[tuple[Ident, Span]]] = {}
        table_span: dict[TableRef, Span] = {}
        edge: EdgeRef | None = None
        for node, o in zip(c.operands, operands, strict=True):
            if isinstance(o, TableRef):
                wanted.setdefault(o, [])
                table_span.setdefault(o, node.span)
            elif isinstance(o, ColumnRef):
                wanted.setdefault(o.table, []).append((o.column, node.span))
                table_span.setdefault(o.table, node.span)
            elif isinstance(node, ast.Edge):
                edge = o
                for side, ref in ((o.left, node.left), (o.right, node.right)):
                    wanted.setdefault(side.table, []).append((side.column, ref.span))
                    table_span.setdefault(side.table, ref.span)
        written = {a.name: a for a in c.attributes}
        for name, attr in attrs.items():
            if attr.type == "COLUMN_LIST" and isinstance(attr.value, tuple):
                target = self.list_table(name, operands)
                if target is not None:
                    span = written[name].value.span
                    wanted[target].extend((i, span) for i in attr.value if isinstance(i, Ident))
        main = next(iter(wanted)) if wanted else None
        if main is not None:
            wanted[main].extend(self.filter_cols)

        unknown = False
        types: dict[tuple[TableRef, Ident], str] = {}
        for table, cols in wanted.items():
            probe = table
            exists = _exists(catalog, probe)
            if exists is False and any(p.quoted for p in table.parts()):
                probe = _unquoted(table)  # a case variant of a quoted name also counts
                exists = _exists(catalog, probe)
            if exists is False:
                self.error("FL311", f"no such table {_show(table)}", table_span[table])
                continue
            known = _columns(catalog, probe)
            if exists is None or known is None:
                unknown = True
            if known is None:
                continue
            reported: set[str] = set()
            for col, span in cols:
                typ = _lookup(known, col)
                if typ is not None:
                    types[(table, col)] = typ
                elif col.text.lower() not in reported:
                    reported.add(col.text.lower())
                    self.error("FL311", f"no such column {col.text} in {_show(table)}", span)

        if main is not None:
            for col, span in self.bool_cols:
                typ = types.get((main, col))
                fam = type_family(typ) if typ is not None else None
                if fam in _NEVER_BOOLEAN:
                    self.error(
                        "FL305",
                        f"{col.text} is a {fam} column ({typ}), not boolean; "
                        "a bare column in a filter must be boolean",
                        span,
                        hint=f"compare it explicitly, e.g. {col.text} IS NOT NULL",
                    )
        if edge is not None and _CAST_ATTR not in attrs:
            lt = types.get((edge.left.table, edge.left.column))
            rt = types.get((edge.right.table, edge.right.column))
            lf = type_family(lt) if lt is not None else None
            rf = type_family(rt) if rt is not None else None
            if lf is not None and rf is not None and lf != rf:
                self.bag.warning(
                    "FL312",
                    f"join compares a {lf} column ({lt}) with a {rf} column ({rt})",
                    c.operands[0].span,
                    hint=f"add {_CAST_ATTR} <family> to compare both sides as one type",
                )
        if unknown:
            self.bag.info(
                "FL313",
                "could not verify table and column names: the catalog does not know them",
                c.span,
            )

    @staticmethod
    def list_table(attr: str, operands: tuple[Operand, ...]) -> TableRef | None:
        for o in operands:
            if isinstance(o, ColumnRef):
                return o.table
            if isinstance(o, EdgeRef):
                return (o.right if attr == _RIGHT_LIST else o.left).table
        return None

    # -- canonical text ----------------------------------------------------------------

    def canonical_text(self, operands: tuple[Operand, ...]) -> str:
        c = self.claim
        nodes: list[ast.Ref | ast.Edge | ast.Literal] = []
        for node, o in zip(c.operands, operands, strict=True):
            span = node.span
            if isinstance(o, TableRef):
                nodes.append(_ast_ref(o.parts(), span))
            elif isinstance(o, ColumnRef):
                nodes.append(_ast_ref((*o.table.parts(), o.column), span))
            else:
                left = _ast_ref((*o.left.table.parts(), o.left.column), span)
                right = _ast_ref((*o.right.table.parts(), o.right.column), span)
                nodes.append(ast.Edge(left, right, span))
        return format_claim(replace(c, operands=tuple(nodes), comment=None))


def _ast_ref(parts: tuple[Ident, ...], span: Span) -> ast.Ref:
    return ast.Ref(tuple(ast.Name(p.text, p.quoted, span) for p in parts), span)


__all__ = ["analyze", "type_family"]
