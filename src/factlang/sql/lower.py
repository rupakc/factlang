"""Lower IR primitives into the internal SQL AST (spec 8.1, 8.2, 18, 19; layer 12).

This module is the single place that encodes the NULL rules of spec 8.1, so every dialect
inherits them, and it knows no dialect: sampling is expressed as AST nodes
(:class:`~factlang.sql.ast.TableSample`, :class:`~factlang.sql.ast.KeyHash`) that printers
spell. The select-list aliases of every lowered plan are exactly its ``result_columns()``
(the wire format a contract's ``derive`` receives, R1).

Every user value becomes a fresh, uniquely named :class:`~factlang.sql.ast.ParamRef`
(``p1``, ``p2``, … in lowering order), so two filters of one plan never clash.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal

from factlang.ir import (
    ALIASES,
    PRIMITIVE_LOWERINGS,
    ColumnAggregate,
    CompositeKeyAggregate,
    KeyOverlap,
    MetadataQuery,
    Metric,
    PatternCoverage,
    ProbePlan,
    SampleSpec,
    ValueMembership,
)
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
from factlang.semantics.refs import Ident, TableRef
from factlang.sql.ast import (
    Agg,
    Aliased,
    And,
    Between,
    Case,
    Cast,
    CatalogColumns,
    Coalesce,
    Column,
    Compare,
    CountIf,
    Expr,
    InList,
    IsNull,
    Join,
    KeyHash,
    Like,
    Literal,
    Lower,
    Not,
    Now,
    Or,
    OrderItem,
    ParamRef,
    Select,
    SplitPrefix,
    SubqueryFrom,
    TableName,
    TableSample,
    Trim,
    UnionAll,
    params_of,
)
from factlang.sql.probe import RenderError

_PREFIX = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

KEYHASH_BUCKETS = 1_000_000
_KEYED = frozenset({Metric.DISTINCT_KEYS, Metric.MAX_PER_KEY, Metric.DUPLICATE_KEYS})
_COUNT_ALL = Agg("COUNT", None)
_NULL_INT = Cast(Literal(None), "integer")
_NULL_TEXT = Cast(Literal(None), "string")
_KEY_ROWS = Ident("key_rows")


def conj(*items: Expr | None) -> Expr | None:
    """AND of the non-``None`` items (``None`` when there are none)."""
    present = tuple(i for i in items if i is not None)
    if not present:
        return None
    return present[0] if len(present) == 1 else And(present)


def keyhash_threshold(fraction: Decimal) -> int:
    """``round_half_up(fraction * 1_000_000)``: a row is kept iff its bucket is below this."""
    return int((fraction * KEYHASH_BUCKETS).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _not_null(cols: Sequence[Ident], qualifier: str | None = None) -> tuple[Expr, ...]:
    return tuple(IsNull(Column(c, qualifier), negated=True) for c in cols)


class Lowerer:
    """Allocates fresh parameter names (``<prefix>1``, ``<prefix>2``, …) for one statement."""

    def __init__(self, param_prefix: str = "p") -> None:
        if not isinstance(param_prefix, str) or not _PREFIX.fullmatch(param_prefix):
            raise ValueError(f"param_prefix {param_prefix!r} must be a plain identifier")
        self._prefix = param_prefix
        self._count = 0

    def param(self, value: object, family: str) -> ParamRef:
        self._count += 1
        return ParamRef(Param(f"{self._prefix}{self._count}", value, family))

    def rename(self, p: Param) -> ParamRef:
        return self.param(p.value, p.family)

    # -- filters ---------------------------------------------------------------------

    def filter(self, expr: FilterExpr, qualifier: str | None = None) -> Expr:
        """Lower a resolved filter; every literal becomes a fresh parameter."""
        if isinstance(expr, FCompare):
            return Compare(expr.op, Column(expr.column, qualifier), self.rename(expr.param))
        if isinstance(expr, FBool):  # R9: ``deleted`` is ``deleted = TRUE``
            return Compare("=", Column(expr.column, qualifier), self.param(True, "boolean"))
        if isinstance(expr, FIn):
            col = Column(expr.column, qualifier)
            if not expr.params:  # x IN () is false, x NOT IN () is true
                return Compare("=", Literal(1), Literal(1 if expr.negated else 0))
            return InList(col, tuple(self.rename(p) for p in expr.params), expr.negated)
        if isinstance(expr, FIsNull):
            return IsNull(Column(expr.column, qualifier), expr.negated)
        if isinstance(expr, FBetween):
            return Between(
                Column(expr.column, qualifier), self.rename(expr.low), self.rename(expr.high)
            )
        if isinstance(expr, (FAnd, FOr)):
            items = tuple(self.filter(i, qualifier) for i in expr.items)
            if len(items) == 1:
                return items[0]
            if isinstance(expr, FAnd):
                return And(items) if items else Compare("=", Literal(1), Literal(1))
            return Or(items) if items else Compare("=", Literal(1), Literal(0))
        if isinstance(expr, FNot):
            return Not(self.filter(expr.item, qualifier))
        raise TypeError(f"not a filter expression: {expr!r}")

    def opt_filter(self, expr: FilterExpr | None) -> Expr | None:
        return None if expr is None else self.filter(expr)

    # -- sampling (Amendment B) --------------------------------------------------------

    def source(
        self,
        table: TableRef,
        spec: SampleSpec | None,
        key_parts: tuple[Expr, ...] | None = None,
    ) -> tuple[TableName, Expr | None]:
        """The FROM item and the WHERE predicate that sample ``table`` per ``spec``.

        Fraction 1 renders no sample (B2). ``keyhash`` excludes rows with a NULL key part,
        then keeps a row iff its bucket is below ``round_half_up(fraction * 1_000_000)``;
        the seed is a string parameter, the threshold an integer parameter. ``key_parts``
        overrides the hashed expressions (one per ``spec.key`` column, before the cast to
        string): KeyOverlap passes its join key after ``cast_to`` so both sides hash alike.
        """
        if spec is None or spec.fraction == 1:
            return TableName(table), None
        if spec.method != "keyhash":
            return TableName(table, sample=TableSample(spec.method, spec.fraction, spec.seed)), None
        parts = key_parts if key_parts is not None else tuple(Column(k) for k in spec.key)
        bucket = KeyHash(
            tuple(
                p if isinstance(p, Cast) and p.family == "string" else Cast(p, "string")
                for p in parts
            ),
            self.param(str(spec.seed), "string"),
        )
        keep = Compare("<", bucket, self.param(keyhash_threshold(spec.fraction), "integer"))
        return TableName(table), conj(*_not_null(spec.key), keep)

    # -- primitives ----------------------------------------------------------------------

    def lower(self, plan: object) -> Select | UnionAll:
        tree = self._dispatch(plan)
        params_of(tree)  # FL500 when one parameter name maps to two different values
        return tree

    def _dispatch(self, plan: object) -> Select | UnionAll:
        _check_single_read(plan)
        if isinstance(plan, ColumnAggregate):
            return self.column_aggregate(plan)
        if isinstance(plan, CompositeKeyAggregate):
            return self.composite_aggregate(plan)
        if isinstance(plan, ValueMembership):
            return self.value_membership(plan)
        if isinstance(plan, KeyOverlap):
            return self.key_overlap(plan)
        if isinstance(plan, PatternCoverage):
            return self.pattern_coverage(plan)
        if isinstance(plan, MetadataQuery):
            return self.metadata(plan)
        return _custom(plan)

    def _aggregate(
        self,
        table: TableRef,
        keys: tuple[Ident, ...],
        flat: list[tuple[str, Expr]],
        keyed: list[tuple[str, Expr]],
        filter_: FilterExpr | None,
        sample: SampleSpec | None,
        order: tuple[str, ...],
    ) -> Select:
        """Flat aggregates over the rows, CROSS JOINed with aggregates over complete keys."""
        from_, sample_where = self.source(table, sample)
        where = conj(self.opt_filter(filter_), sample_where)
        if sample is not None:
            flat.append(("sampled_rows", _COUNT_ALL))
        flat_select = Select(tuple(Aliased(e, a) for a, e in flat), from_, where) if flat else None
        if not keyed:
            assert flat_select is not None
            return flat_select
        # MAX_PER_KEY / DUPLICATE_KEYS / composite DISTINCT_KEYS: group by the key columns
        # after excluding rows with any NULL key part (spec 8.1); never concatenate.
        groups = Select(
            (Aliased(_COUNT_ALL, "key_rows"),),
            from_,
            conj(*_not_null(keys), where),
            group_by=tuple(Column(k) for k in keys),
        )
        keyed_select = Select(tuple(Aliased(e, a) for a, e in keyed), SubqueryFrom(groups, "g"))
        if flat_select is None:
            return keyed_select
        source = {a: "a" for a, _ in flat} | {a: "b" for a, _ in keyed}
        return Select(
            tuple(Aliased(Column(Ident(a), source[a]), a) for a in order),
            Join(SubqueryFrom(flat_select, "a"), SubqueryFrom(keyed_select, "b"), None, "CROSS"),
        )

    @staticmethod
    def _keyed_expr(metric: Metric) -> Expr:
        rows = Column(_KEY_ROWS, "g")
        if metric is Metric.DISTINCT_KEYS:
            return _COUNT_ALL
        if metric is Metric.MAX_PER_KEY:
            return Agg("MAX", rows)
        return CountIf(Compare(">", rows, Literal(1)))  # DUPLICATE_KEYS

    def column_aggregate(self, plan: ColumnAggregate) -> Select:
        col = Column(plan.column) if plan.column is not None else None
        flat: list[tuple[str, Expr]] = []
        keyed: list[tuple[str, Expr]] = []
        for m in plan.metrics:
            alias = ALIASES[m]
            if m in (Metric.MAX_PER_KEY, Metric.DUPLICATE_KEYS):
                keyed.append((alias, self._keyed_expr(m)))
            elif m is Metric.ROW_COUNT:
                flat.append((alias, _COUNT_ALL))
            elif m is Metric.NOW:
                flat.append((alias, Now()))
            else:
                assert col is not None  # the primitive validates column-needing metrics
                fn = {
                    Metric.NON_NULL: Agg("COUNT", col),
                    Metric.DISTINCT_KEYS: Agg("COUNT", col, distinct=True),
                    Metric.MIN_VALUE: Agg("MIN", col),
                    Metric.MAX_VALUE: Agg("MAX", col),
                    Metric.SUM: Agg("SUM", col),
                    Metric.AVG: Agg("AVG", col),
                }
                flat.append((alias, fn[m]))
        keys = (plan.column,) if plan.column is not None else ()
        return self._aggregate(
            plan.table, keys, flat, keyed, plan.filter, plan.sample, plan.result_columns()
        )

    def composite_aggregate(self, plan: CompositeKeyAggregate) -> Select:
        flat: list[tuple[str, Expr]] = []
        keyed: list[tuple[str, Expr]] = []
        for m in plan.metrics:
            alias = ALIASES[m]
            if m in _KEYED:
                keyed.append((alias, self._keyed_expr(m)))
            elif m is Metric.ROW_COUNT:
                flat.append((alias, _COUNT_ALL))
            else:  # NON_NULL over a tuple: rows with a complete key
                complete = conj(*_not_null(plan.columns))
                assert complete is not None  # the primitive requires at least one column
                flat.append((alias, CountIf(complete)))
        return self._aggregate(
            plan.table, plan.columns, flat, keyed, plan.filter, plan.sample, plan.result_columns()
        )

    def value_membership(self, plan: ValueMembership) -> Select:
        col = Column(plan.column)
        value: Expr = Cast(col, "string")
        if plan.split is not None:
            value = Trim(SplitPrefix(value, self.param(plan.split, "string")))
        declared = tuple(self.param(v, "string") for v in plan.declared)
        from_, sample_where = self.source(plan.table, plan.sample)
        where = conj(self.opt_filter(plan.filter), sample_where)
        sampled = plan.sample is not None
        if declared:
            declared_rows: Expr = CountIf(InList(value, declared))
            undeclared_distinct: Expr = Agg(
                "COUNT", Case(((InList(value, declared, negated=True), value),)), distinct=True
            )
        else:  # R2: nothing declared, every non-NULL value is undeclared
            declared_rows = Literal(0)
            undeclared_distinct = Agg("COUNT", value, distinct=True)
        summary = [
            Aliased(Literal("summary"), "kind"),
            Aliased(Agg("COUNT", col), "non_null"),
            Aliased(declared_rows, "declared_rows"),
            Aliased(undeclared_distinct, "undeclared_distinct"),
        ]
        ranked = [
            Aliased(Literal("undeclared"), "kind"),
            Aliased(_NULL_INT, "non_null"),
            Aliased(_NULL_INT, "declared_rows"),
            Aliased(_NULL_INT, "undeclared_distinct"),
        ]
        if sampled:
            summary.append(Aliased(_COUNT_ALL, "sampled_rows"))
            ranked.append(Aliased(_NULL_INT, "sampled_rows"))
        # The ranked list projects the compared value in an inner select and groups by the
        # plain column: GROUP BY must never repeat an expression holding a bind parameter
        # (the SPLIT separator), which engines cannot prove equal to the SELECT's.
        notin = InList(value, declared, negated=True) if declared else None
        values = Select(
            (Aliased(value, "value"),), from_, conj(IsNull(col, negated=True), notin, where)
        )
        grouped = Column(Ident("value"), "v")
        summary += [Aliased(_NULL_TEXT, "value"), Aliased(_NULL_INT, "freq")]
        ranked += [Aliased(grouped, "value"), Aliased(_COUNT_ALL, "freq")]
        union = UnionAll(
            (
                Select(tuple(summary), from_, where),
                Select(
                    tuple(ranked),
                    SubqueryFrom(values, "v"),
                    group_by=(grouped,),
                    order_by=(OrderItem(_COUNT_ALL, descending=True), OrderItem(grouped)),
                    limit=plan.report_limit,
                ),
            )
        )
        # Row order is part of the result: summary first, then freq desc, value asc. The
        # outer ORDER BY has no LIMIT; the only LIMIT stays on the ranked branch.
        u = "u"
        kind = Column(Ident("kind"), u)
        summary_first = Case(((Compare("=", kind, Literal("summary")), Literal(0)),), Literal(1))
        return Select(
            tuple(Aliased(Column(Ident(a), u), a) for a in union.aliases),
            SubqueryFrom(union, u),
            order_by=(
                OrderItem(summary_first),
                OrderItem(Column(Ident("freq"), u), descending=True),
                OrderItem(Column(Ident("value"), u)),
            ),
        )

    def key_overlap(self, plan: KeyOverlap) -> Select:
        n = len(plan.left)
        names = [Ident(f"k{i}") for i in range(1, n + 1)]

        def key(c: Ident) -> Expr:
            return Cast(Column(c), plan.cast_to) if plan.cast_to is not None else Column(c)

        def side(
            table: TableRef,
            cols: tuple[Ident, ...],
            filter_: FilterExpr | None,
            spec: SampleSpec | None,
            with_rows: bool,
        ) -> tuple[Select, Select | None]:
            keys = tuple(key(c) for c in cols)
            # keyhash over the join key hashes the key as compared (after cast_to)
            parts = keys if spec is not None and spec.key == cols else None
            from_, sample_where = self.source(table, spec, parts)
            where = conj(self.opt_filter(filter_), sample_where)
            columns = [Aliased(k, nm.text) for k, nm in zip(keys, names, strict=True)]
            if with_rows:
                columns.append(Aliased(_COUNT_ALL, "key_rows"))
            grouped = Select(tuple(columns), from_, conj(*_not_null(cols), where), group_by=keys)
            sampled = (
                Select((Aliased(_COUNT_ALL, "sampled_rows"),), from_, where)
                if spec is not None
                else None
            )
            return grouped, sampled

        left_cols = tuple(c.column for c in plan.left)
        right_cols = tuple(c.column for c in plan.right)
        mult = plan.measure_multiplicity
        lsel, lsampled = side(plan.left_table, left_cols, plan.filter, plan.sample, True)
        rsel, rsampled = side(
            plan.right_table, right_cols, plan.right_filter, plan.right_sample, mult
        )

        l_rows, r_first = Column(_KEY_ROWS, "l"), Column(names[0], "r")
        on = conj(*(Compare("=", Column(k, "l"), Column(k, "r")) for k in names))
        assert on is not None
        matched_rows = Case(((IsNull(r_first, negated=True), l_rows),), Literal(0))
        left_cols_out = [
            Aliased(_COUNT_ALL, "distinct_left"),
            Aliased(Agg("COUNT", r_first), "matched"),
            Aliased(Coalesce(Agg("SUM", l_rows), Literal(0)), "left_non_null"),
            Aliased(Coalesce(Agg("SUM", matched_rows), Literal(0)), "matched_left_rows"),
        ]
        right_cols_out = [Aliased(_COUNT_ALL, "distinct_right")]
        if mult:
            left_cols_out.append(Aliased(Agg("MAX", l_rows), "max_left_per_key"))
            right_cols_out.append(Aliased(Agg("MAX", Column(_KEY_ROWS, "r")), "max_right_per_key"))
        overlap = Select(
            tuple(left_cols_out),
            Join(SubqueryFrom(lsel, "l"), SubqueryFrom(rsel, "r"), on, "LEFT"),
        )
        right_stats = Select(tuple(right_cols_out), SubqueryFrom(rsel, "r"))
        parts: list[tuple[str, Select, tuple[str, ...]]] = [
            ("a", overlap, tuple(c.alias for c in left_cols_out)),
            ("b", right_stats, tuple(c.alias for c in right_cols_out)),
        ]
        if lsampled is not None:
            parts.append(("c", _rename(lsampled, "sampled_left_rows"), ("sampled_left_rows",)))
        if rsampled is not None:
            parts.append(("d", _rename(rsampled, "sampled_right_rows"), ("sampled_right_rows",)))
        source = {a: q for q, _, aliases in parts for a in aliases}
        from_item: Join | SubqueryFrom = SubqueryFrom(parts[0][1], parts[0][0])
        for q, sel, _ in parts[1:]:
            from_item = Join(from_item, SubqueryFrom(sel, q), None, "CROSS")
        return Select(
            tuple(Aliased(Column(Ident(a), source[a]), a) for a in plan.result_columns()),
            from_item,
        )

    def pattern_coverage(self, plan: PatternCoverage) -> Select:
        col = Column(plan.column)
        from_, sample_where = self.source(plan.table, plan.sample)
        where = conj(self.opt_filter(plan.filter), sample_where)
        columns = [
            Aliased(Agg("COUNT", col), "non_null"),
            Aliased(
                CountIf(Like(Lower(col), self.param(plan.pattern.lower(), "string"))), "matching"
            ),
        ]
        if plan.sample is not None:
            columns.append(Aliased(_COUNT_ALL, "sampled_rows"))
        return Select(tuple(columns), from_, where)

    @staticmethod
    def metadata(plan: MetadataQuery) -> Select:
        return Select(
            (
                Aliased(Column(Ident("column_name")), "column_name"),
                Aliased(Column(Ident("data_type")), "data_type"),
            ),
            CatalogColumns(plan.table, plan.column),
        )


def _multi_read(plan: object) -> bool:
    """True when the lowering reads the (sampled) table more than once."""
    if isinstance(plan, (ValueMembership, KeyOverlap)):
        return True
    if isinstance(plan, ColumnAggregate):
        return any(m in (Metric.MAX_PER_KEY, Metric.DUPLICATE_KEYS) for m in plan.metrics)
    if isinstance(plan, CompositeKeyAggregate):
        return any(m in _KEYED for m in plan.metrics)
    return False


def _check_single_read(plan: object) -> None:
    """FL405: a block/row sample is not guaranteed to repeat across reads; keyhash is."""
    specs = [getattr(plan, "sample", None), getattr(plan, "right_sample", None)]
    methods = sorted(
        {
            s.method
            for s in specs
            if isinstance(s, SampleSpec) and s.method != "keyhash" and s.fraction != 1
        }
    )
    if methods and _multi_read(plan):
        raise RenderError(
            f"FL405 sample method {methods[0]} needs a single read; use keyhash: "
            f"{type(plan).__name__} reads the sampled table more than once"
        )


def _rename(select: Select, alias: str) -> Select:
    (only,) = select.columns
    return Select((Aliased(only.expr, alias),), select.from_, select.where)


def _custom(plan: object) -> Select | UnionAll:
    builder = PRIMITIVE_LOWERINGS.get(type(plan))
    if builder is None:
        raise RenderError(
            f"FL500 primitive {type(plan).__name__} is not renderable: "
            "register it with factlang.ir.register_primitive(cls, lower=...)"
        )
    tree = builder(plan)
    if not isinstance(tree, (Select, UnionAll)):
        raise RenderError(
            f"FL500 lowering of {type(plan).__name__} returned {type(tree).__name__}, "
            "not a Select or UnionAll"
        )
    expected = tuple(plan.result_columns())  # type: ignore[attr-defined]
    if tree.aliases != expected:
        raise RenderError(
            f"FL500 lowering of {type(plan).__name__} selects {list(tree.aliases)}, "
            f"but result_columns() is {list(expected)}"
        )
    return tree


def lower(plan: ProbePlan) -> Select | UnionAll:
    """Lower one primitive (core or registered) to the internal SQL AST."""
    return Lowerer().lower(plan)


__all__ = ["KEYHASH_BUCKETS", "Lowerer", "conj", "keyhash_threshold", "lower"]
