"""Lowering snapshots as structure assertions (spec 8.1, 8.2, 18, 19)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

import pytest

from factlang.ir import (
    PRIMITIVE_LOWERINGS,
    Arity,
    ColumnAggregate,
    CompositeKeyAggregate,
    KeyOverlap,
    MetadataQuery,
    Metric,
    PatternCoverage,
    SampleSpec,
    ValueMembership,
    register_primitive,
)
from factlang.semantics.filters import FAnd, FBool, FCompare, FIn, FNot, Param
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef
from factlang.sql import ast as A
from factlang.sql.fragment import JoinEdge, RenderedFragment, lower_filter, lower_join
from factlang.sql.lower import lower
from factlang.sql.probe import RenderError

ORDERS = TableRef(None, None, Ident("orders"))
CUSTOMERS = TableRef(None, Ident("shop"), Ident("customers"))
ITEMS = TableRef(None, None, Ident("order_items"))
ID, STATUS = Ident("id"), Ident("status")
HOSTILE = "x'); DROP TABLE orders; --"


def cref(t: TableRef, name: str) -> ColumnRef:
    return ColumnRef(t, Ident(name))


def status_is(value: object = "paid", name: str = "p1") -> FCompare:
    return FCompare(STATUS, "=", Param(name, value, "string"))


def of(tree: object, cls: type) -> list[object]:
    return [n for n in A.walk(tree) if isinstance(n, cls)]


def selects(tree: object) -> list[A.Select]:
    return [n for n in A.walk(tree) if isinstance(n, A.Select)]


def grouped(tree: object) -> list[A.Select]:
    return [s for s in selects(tree) if s.group_by]


KEYHASH = SampleSpec("keyhash", Decimal("0.01"), seed=42, key=(ID,))
SYSTEM = SampleSpec("system", Decimal("0.05"), seed=7)

EXACT_PLANS: list[object] = [
    ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT, Metric.NOW)),
    ColumnAggregate(
        ORDERS,
        ID,
        (
            Metric.ROW_COUNT,
            Metric.NON_NULL,
            Metric.DISTINCT_KEYS,
            Metric.MAX_PER_KEY,
            Metric.MIN_VALUE,
            Metric.MAX_VALUE,
            Metric.SUM,
            Metric.AVG,
            Metric.DUPLICATE_KEYS,
            Metric.NOW,
        ),
        filter=status_is(),
    ),
    ColumnAggregate(ORDERS, ID, (Metric.MAX_PER_KEY, Metric.DUPLICATE_KEYS)),
    CompositeKeyAggregate(
        ITEMS,
        (Ident("order_id"), Ident("line")),
        (
            Metric.ROW_COUNT,
            Metric.NON_NULL,
            Metric.DISTINCT_KEYS,
            Metric.MAX_PER_KEY,
            Metric.DUPLICATE_KEYS,
        ),
    ),
    ValueMembership(ORDERS, STATUS, ("paid", "open")),
    ValueMembership(ORDERS, STATUS, (), report_limit=5),
    ValueMembership(ORDERS, STATUS, ("other",), split=":", filter=status_is()),
    KeyOverlap((cref(ITEMS, "order_id"),), (cref(ORDERS, "id"),)),
    KeyOverlap(
        (cref(ITEMS, "order_id"), cref(ITEMS, "shop_id")),
        (cref(ORDERS, "id"), cref(ORDERS, "shop_id")),
        cast_to="string",
        measure_multiplicity=True,
        filter=status_is(),
        right_filter=status_is("open"),
    ),
    PatternCoverage(CUSTOMERS, Ident("email"), "%@%.COM", filter=status_is()),
    MetadataQuery(ORDERS),
    MetadataQuery(ORDERS, ID),
]


def sampled(plan: object, spec: SampleSpec) -> object:
    if isinstance(plan, KeyOverlap):
        return dataclasses.replace(plan, sample=spec, right_sample=spec)
    return dataclasses.replace(plan, sample=spec)  # type: ignore[type-var]


SAMPLED_PLANS: list[object] = [
    sampled(p, s)
    for p in EXACT_PLANS
    if not isinstance(p, MetadataQuery)
    for s in (KEYHASH, SYSTEM)
]


# --- wire format (R1: the payload is what derive receives) -------------------------------


@pytest.mark.parametrize("plan", EXACT_PLANS + SAMPLED_PLANS, ids=repr)
def test_aliases_are_exactly_the_result_columns(plan: object) -> None:
    tree = lower(plan)  # type: ignore[arg-type]
    assert tree.aliases == plan.result_columns()  # type: ignore[attr-defined]


def test_flat_column_aggregate() -> None:
    plan = ColumnAggregate(ORDERS, ID, (Metric.ROW_COUNT, Metric.NON_NULL, Metric.MIN_VALUE))
    tree = lower(plan)
    assert isinstance(tree, A.Select)
    assert tree.from_ == A.TableName(ORDERS)
    assert tree.where is None and tree.group_by == () and tree.limit is None
    exprs = [c.expr for c in tree.columns]
    assert exprs == [
        A.Agg("COUNT", None),
        A.Agg("COUNT", A.Column(ID)),
        A.Agg("MIN", A.Column(ID)),
    ]


def test_column_aggregate_metrics_map_to_aggregates() -> None:
    plan = ColumnAggregate(
        ORDERS, ID, (Metric.DISTINCT_KEYS, Metric.SUM, Metric.AVG, Metric.MAX_VALUE, Metric.NOW)
    )
    tree = lower(plan)
    assert isinstance(tree, A.Select)
    assert [c.expr for c in tree.columns] == [
        A.Agg("COUNT", A.Column(ID), distinct=True),
        A.Agg("SUM", A.Column(ID)),
        A.Agg("AVG", A.Column(ID)),
        A.Agg("MAX", A.Column(ID)),
        A.Now(),
    ]


# --- NULL rules ---------------------------------------------------------------------------


def test_max_per_key_inner_where_excludes_null_key() -> None:
    tree = lower(ColumnAggregate(ORDERS, ID, (Metric.ROW_COUNT, Metric.MAX_PER_KEY)))
    (inner,) = grouped(tree)
    assert inner.where is not None
    assert A.IsNull(A.Column(ID), negated=True) in of(inner.where, A.IsNull)
    assert inner.group_by == (A.Column(ID),)


def test_composite_max_per_key_excludes_rows_with_any_null_key_part() -> None:
    keys = (Ident("order_id"), Ident("line"), Ident("shop_id"))
    tree = lower(CompositeKeyAggregate(ITEMS, keys, (Metric.MAX_PER_KEY,), filter=status_is()))
    (inner,) = grouped(tree)
    nulls = of(inner.where, A.IsNull)
    for k in keys:
        assert A.IsNull(A.Column(k), negated=True) in nulls
    assert inner.group_by == tuple(A.Column(k) for k in keys)
    # the claim's filter is applied inside, before grouping
    assert of(inner.where, A.ParamRef)


def test_composite_distinct_keys_counts_groups_never_concatenates() -> None:
    keys = (Ident("order_id"), Ident("line"))
    tree = lower(CompositeKeyAggregate(ITEMS, keys, (Metric.DISTINCT_KEYS,)))
    assert not [a for a in of(tree, A.Agg) if a.distinct]  # type: ignore[attr-defined]
    (inner,) = grouped(tree)
    assert inner.group_by == tuple(A.Column(k) for k in keys)
    outer = tree
    assert isinstance(outer, A.Select)
    assert outer.columns[0] == A.Aliased(A.Agg("COUNT", None), "distinct_keys")


def test_composite_non_null_counts_rows_with_complete_key() -> None:
    keys = (Ident("order_id"), Ident("line"))
    tree = lower(CompositeKeyAggregate(ITEMS, keys, (Metric.NON_NULL,)))
    assert isinstance(tree, A.Select)
    assert tree.columns[0].expr == A.CountIf(
        A.And(tuple(A.IsNull(A.Column(k), negated=True) for k in keys))
    )


def test_pattern_coverage_is_case_folded_like_over_a_lowercased_param() -> None:
    tree = lower(PatternCoverage(CUSTOMERS, Ident("email"), "%@%.COM"))
    assert isinstance(tree, A.Select)
    assert tree.aliases == ("non_null", "matching")
    assert tree.columns[0].expr == A.Agg("COUNT", A.Column(Ident("email")))
    cond = tree.columns[1].expr
    assert isinstance(cond, A.CountIf) and isinstance(cond.cond, A.Like)
    assert cond.cond.e == A.Lower(A.Column(Ident("email")))
    assert cond.cond.pattern.param.value == "%@%.com"
    assert cond.cond.pattern.param.family == "string"


# --- ValueMembership -----------------------------------------------------------------------


def test_value_membership_is_summary_union_ranked_list() -> None:
    tree = lower(ValueMembership(ORDERS, STATUS, ("paid", "open"), report_limit=7))
    assert isinstance(tree, A.UnionAll)
    summary, ranked = tree.selects
    assert summary.columns[0].expr == A.Literal("summary")
    assert ranked.columns[0].expr == A.Literal("undeclared")
    assert summary.limit is None and ranked.limit == 7
    assert [o.descending for o in ranked.order_by] == [True, False]
    assert ranked.order_by[0].expr == A.Agg("COUNT", None)
    # the compared value is projected in an inner select and grouped as a plain column, so
    # GROUP BY never repeats an expression holding a bind parameter (SPLIT separator)
    assert isinstance(ranked.from_, A.SubqueryFrom)
    inner = ranked.from_.select
    assert inner.columns == (A.Aliased(A.Cast(A.Column(STATUS), "string"), "value"),)
    assert inner.group_by == () and inner.limit is None
    value = A.Column(Ident("value"), ranked.from_.alias)
    assert ranked.order_by[1].expr == value
    assert ranked.group_by == (value,)
    assert ranked.where is None
    (notin,) = [i for i in of(inner.where, A.InList) if i.negated]  # type: ignore[attr-defined]
    assert [p.param.value for p in notin.items] == ["paid", "open"]  # type: ignore[attr-defined]
    assert A.IsNull(A.Column(STATUS), negated=True) in of(inner.where, A.IsNull)


def test_ranked_group_by_never_holds_a_param() -> None:
    tree = lower(ValueMembership(ORDERS, STATUS, ("other",), split=":", filter=status_is()))
    assert isinstance(tree, A.UnionAll)
    for s in selects(tree):
        for g in s.group_by:
            assert not of(g, A.ParamRef)


def test_value_membership_empty_declared_has_no_in_list() -> None:
    tree = lower(ValueMembership(ORDERS, STATUS, ()))
    assert not of(tree, A.InList)
    assert isinstance(tree, A.UnionAll)
    summary, ranked = tree.selects
    exprs = dict(zip(summary.aliases, (c.expr for c in summary.columns), strict=True))
    assert exprs["declared_rows"] == A.Literal(0)
    assert isinstance(ranked.from_, A.SubqueryFrom)
    assert ranked.from_.select.where == A.IsNull(A.Column(STATUS), negated=True)


def test_value_membership_split_compares_trimmed_prefix() -> None:
    tree = lower(ValueMembership(ORDERS, STATUS, ("other",), split=":"))
    prefixes = of(tree, A.SplitPrefix)
    assert prefixes
    for sp in prefixes:
        assert sp.separator.param.value == ":"  # type: ignore[attr-defined]
    assert A.Trim(prefixes[0]) in of(tree, A.Trim)  # type: ignore[arg-type]


def test_value_membership_shares_declared_params_between_branches() -> None:
    tree = lower(ValueMembership(ORDERS, STATUS, ("paid",)))
    assert [p.value for p in A.params_of(tree)] == ["paid"]


# --- limits and sampling appear only where allowed -----------------------------------------


@pytest.mark.parametrize("plan", EXACT_PLANS + SAMPLED_PLANS, ids=repr)
def test_limit_only_on_value_membership_ranked_list(plan: object) -> None:
    tree = lower(plan)  # type: ignore[arg-type]
    limited = [s for s in selects(tree) if s.limit is not None]
    if isinstance(plan, ValueMembership):
        assert isinstance(tree, A.UnionAll)
        assert limited == [tree.selects[1]]
        assert tree.selects[1].limit == plan.report_limit
    else:
        assert limited == []


@pytest.mark.parametrize("plan", EXACT_PLANS, ids=repr)
def test_no_sampling_nodes_in_exact_plans(plan: object) -> None:
    tree = lower(plan)  # type: ignore[arg-type]
    assert not of(tree, A.TableSample) and not of(tree, A.KeyHash)


@pytest.mark.parametrize("plan", SAMPLED_PLANS, ids=repr)
def test_sampled_plans_carry_their_sample(plan: object) -> None:
    tree = lower(plan)  # type: ignore[arg-type]
    spec = plan.sample  # type: ignore[attr-defined]
    if spec.method == "keyhash":
        assert of(tree, A.KeyHash) and not of(tree, A.TableSample)
    else:
        assert of(tree, A.TableSample) and not of(tree, A.KeyHash)


def test_keyhash_predicate_shape() -> None:
    spec = SampleSpec("keyhash", Decimal("0.0123445"), seed=42, key=(ID, Ident("shop_id")))
    tree = lower(ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,), sample=spec))
    assert isinstance(tree, A.Select)
    (cmp,) = [c for c in of(tree, A.Compare) if isinstance(c.left, A.KeyHash)]  # type: ignore[attr-defined]
    assert cmp.op == "<"  # type: ignore[attr-defined]
    threshold = cmp.right.param  # type: ignore[attr-defined]
    assert (
        threshold.family == "integer" and threshold.value == 12345
    )  # half-up (half-even gives 12344)
    kh = cmp.left  # type: ignore[attr-defined]
    assert kh.seed.param.family == "string" and kh.seed.param.value == "42"
    assert kh.parts == (
        A.Cast(A.Column(ID), "string"),
        A.Cast(A.Column(Ident("shop_id")), "string"),
    )
    nulls = of(tree.where, A.IsNull)
    assert A.IsNull(A.Column(ID), negated=True) in nulls
    assert A.IsNull(A.Column(Ident("shop_id")), negated=True) in nulls
    assert tree.aliases == ("row_count", "sampled_rows")


def test_system_sample_sits_on_the_from_item() -> None:
    tree = lower(PatternCoverage(ORDERS, STATUS, "%a%", sample=SYSTEM))
    assert isinstance(tree, A.Select)
    assert tree.from_ == A.TableName(ORDERS, sample=A.TableSample("system", Decimal("0.05"), 7))
    assert tree.columns[-1] == A.Aliased(A.Agg("COUNT", None), "sampled_rows")


def test_fraction_one_renders_no_sample_but_reports_sampled_rows() -> None:
    for method, key in (("keyhash", (ID,)), ("bernoulli", ())):
        spec = SampleSpec(method, Decimal(1), seed=3, key=key)  # type: ignore[arg-type]
        tree = lower(ColumnAggregate(ORDERS, ID, (Metric.NON_NULL,), sample=spec))
        assert not of(tree, A.TableSample) and not of(tree, A.KeyHash)
        assert not of(tree, A.IsNull)
        assert tree.aliases == ("non_null", "sampled_rows")


def test_value_membership_sampled_rows_only_on_summary() -> None:
    tree = lower(ValueMembership(ORDERS, STATUS, ("paid",), sample=SYSTEM))
    assert isinstance(tree, A.UnionAll)
    summary, ranked = tree.selects
    i = summary.aliases.index("sampled_rows")
    assert summary.columns[i].expr == A.Agg("COUNT", None)
    assert ranked.columns[i].expr == A.Cast(A.Literal(None), "integer")
    sampled_from = A.TableName(ORDERS, sample=A.TableSample("system", Decimal("0.05"), 7))
    assert summary.from_ == sampled_from
    assert isinstance(ranked.from_, A.SubqueryFrom)
    assert ranked.from_.select.from_ == sampled_from


# --- KeyOverlap -----------------------------------------------------------------------------


def _side_selects(tree: object, table: TableRef) -> list[A.Select]:
    return [s for s in grouped(tree) if isinstance(s.from_, A.TableName) and s.from_.ref == table]


def test_key_overlap_composite_tuples_null_parts_excluded_and_cast() -> None:
    plan = KeyOverlap(
        (cref(ITEMS, "order_id"), cref(ITEMS, "shop_id")),
        (cref(ORDERS, "id"), cref(ORDERS, "shop_id")),
        cast_to="string",
        filter=status_is("paid", "p1"),
        right_filter=status_is("open", "p1"),
    )
    tree = lower(plan)
    (left,) = _side_selects(tree, ITEMS)
    rights = _side_selects(tree, ORDERS)
    assert rights
    for name in ("order_id", "shop_id"):
        assert A.IsNull(A.Column(Ident(name)), negated=True) in of(left.where, A.IsNull)
    assert left.group_by == (
        A.Cast(A.Column(Ident("order_id")), "string"),
        A.Cast(A.Column(Ident("shop_id")), "string"),
    )
    for right in rights:
        for name in ("id", "shop_id"):
            assert A.IsNull(A.Column(Ident(name)), negated=True) in of(right.where, A.IsNull)
        assert [p.value for p in A.params_of(right.where)] == ["open"]
    assert [p.value for p in A.params_of(left.where)] == ["paid"]
    # part-wise comparison, never concatenation
    (join,) = [j for j in of(tree, A.Join) if j.kind == "LEFT"]  # type: ignore[attr-defined]
    eqs = of(join.on, A.Compare)  # type: ignore[attr-defined]
    assert len(eqs) == 2 and all(e.op == "=" for e in eqs)  # type: ignore[attr-defined]


def test_key_overlap_renames_clashing_param_names() -> None:
    plan = KeyOverlap(
        (cref(ITEMS, "order_id"),),
        (cref(ORDERS, "id"),),
        filter=status_is("paid", "p1"),
        right_filter=status_is("open", "p1"),
    )
    params = A.params_of(lower(plan))
    assert len({p.name for p in params}) == len(params)
    assert sorted(p.value for p in params) == ["open", "paid"]  # type: ignore[type-var]


def test_key_overlap_multiplicity() -> None:
    plan = KeyOverlap((cref(ITEMS, "order_id"),), (cref(ORDERS, "id"),), measure_multiplicity=True)
    tree = lower(plan)
    assert tree.aliases[-2:] == ("max_left_per_key", "max_right_per_key")
    assert len([a for a in of(tree, A.Agg) if a.fn == "MAX"]) == 2  # type: ignore[attr-defined]


def test_key_overlap_keyhash_both_sides_same_threshold_and_seed() -> None:
    lk = SampleSpec("keyhash", Decimal("0.1"), seed=5, key=(Ident("order_id"),))
    rk = SampleSpec("keyhash", Decimal("0.1"), seed=5, key=(ID,))
    plan = KeyOverlap((cref(ITEMS, "order_id"),), (cref(ORDERS, "id"),), sample=lk, right_sample=rk)
    tree = lower(plan)
    hashes = of(tree, A.KeyHash)
    assert {h.seed.param.value for h in hashes} == {"5"}  # type: ignore[attr-defined]
    thresholds = {
        c.right.param.value  # type: ignore[attr-defined]
        for c in of(tree, A.Compare)
        if isinstance(c.left, A.KeyHash)  # type: ignore[attr-defined]
    }
    assert thresholds == {100000}
    assert tree.aliases[-2:] == ("sampled_left_rows", "sampled_right_rows")


# --- filters --------------------------------------------------------------------------------


def test_bool_atom_lowers_to_equality_with_a_true_param() -> None:
    expr = lower_filter(FNot(FBool(Ident("deleted"))), None)
    assert isinstance(expr, A.Not) and isinstance(expr.e, A.Compare)
    assert expr.e.left == A.Column(Ident("deleted"))
    assert isinstance(expr.e.right, A.ParamRef)
    assert expr.e.right.param.value is True and expr.e.right.param.family == "boolean"


def test_filter_lowering_qualifies_with_alias() -> None:
    f = FAnd((status_is(), FIn(ID, (Param("p2", 1, "integer"), Param("p3", 2, "integer")))))
    expr = lower_filter(f, "o")
    assert isinstance(expr, A.And)
    assert {c.qualifier for c in of(expr, A.Column)} == {"o"}  # type: ignore[attr-defined]


def test_hostile_literal_is_only_ever_inside_a_param() -> None:
    plans = [
        ColumnAggregate(ORDERS, ID, (Metric.ROW_COUNT,), filter=status_is(HOSTILE)),
        ValueMembership(ORDERS, STATUS, (HOSTILE,), filter=status_is(HOSTILE)),
        PatternCoverage(ORDERS, STATUS, HOSTILE),
        KeyOverlap(
            (cref(ITEMS, "order_id"),), (cref(ORDERS, "id"),), right_filter=status_is(HOSTILE)
        ),
    ]
    for plan in plans:
        tree = lower(plan)
        found_in_param = False
        for n in A.walk(tree):
            if isinstance(n, A.ParamRef):
                found_in_param = found_in_param or HOSTILE.lower() in str(n.param.value).lower()
                continue
            if dataclasses.is_dataclass(n):
                for f in dataclasses.fields(n):
                    v = getattr(n, f.name)
                    assert not (isinstance(v, str) and HOSTILE.lower() in v.lower()), (plan, n)
        assert found_in_param


# --- metadata and custom primitives ---------------------------------------------------------


def test_metadata_query_selects_from_catalog_columns() -> None:
    tree = lower(MetadataQuery(ORDERS, ID))
    assert isinstance(tree, A.Select)
    assert tree.from_ == A.CatalogColumns(ORDERS, ID)
    assert tree.aliases == ("column_name", "data_type")


@dataclass(frozen=True)
class _MaxLen:
    arity: ClassVar[Arity] = Arity.SINGLE_ROW
    table: TableRef

    def result_columns(self) -> tuple[str, ...]:
        return ("row_count",)


@dataclass(frozen=True)
class _Unregistered:
    arity: ClassVar[Arity] = Arity.SINGLE_ROW

    def result_columns(self) -> tuple[str, ...]:
        return ("x",)


def test_custom_primitive_dispatches_through_registry() -> None:
    def build(plan: _MaxLen) -> A.Select:
        return A.Select((A.Aliased(A.Agg("COUNT", None), "row_count"),), A.TableName(plan.table))

    register_primitive(_MaxLen, lower=build, replace=True)
    try:
        tree = lower(_MaxLen(ORDERS))  # type: ignore[arg-type]
        assert tree == A.Select(
            (A.Aliased(A.Agg("COUNT", None), "row_count"),), A.TableName(ORDERS)
        )
        register_primitive(_MaxLen, lower=lambda plan: "SELECT 1", replace=True)
        with pytest.raises(RenderError, match="FL500"):
            lower(_MaxLen(ORDERS))  # type: ignore[arg-type]
    finally:
        PRIMITIVE_LOWERINGS.pop(_MaxLen, None)


def test_unknown_primitive_is_fl500() -> None:
    with pytest.raises(RenderError, match="FL500"):
        lower(_Unregistered())  # type: ignore[arg-type]


# --- AST guards -----------------------------------------------------------------------------


def test_literal_refuses_user_values() -> None:
    for ok in (0, 1, "summary", "undeclared", 1_000_000, None):
        A.Literal(ok)
    for bad in ("paid", 2, True, HOSTILE, 1.0):
        with pytest.raises(ValueError):
            A.Literal(bad)


def test_union_all_branches_must_agree_on_aliases() -> None:
    a = A.Select((A.Aliased(A.Literal(1), "x"),), A.TableName(ORDERS))
    b = A.Select((A.Aliased(A.Literal(1), "y"),), A.TableName(ORDERS))
    with pytest.raises(ValueError):
        A.UnionAll((a, b))


# --- fragments (R6) -------------------------------------------------------------------------


def test_join_path_left_filter_in_where_others_in_on() -> None:
    e1 = EdgeRef(cref(ITEMS, "order_id"), cref(ORDERS, "id"))
    e2 = EdgeRef(cref(ORDERS, "customer_id"), cref(CUSTOMERS, "id"))
    from_, where = lower_join(
        [e1, e2],
        filters={ITEMS: status_is("a", "p1"), CUSTOMERS: status_is("b", "p1")},
        aliases={ITEMS: "i", ORDERS: "o", CUSTOMERS: "c"},
    )
    assert isinstance(from_, A.Join) and from_.kind == "INNER"
    assert isinstance(from_.left, A.Join)
    assert from_.left.left == A.TableName(ITEMS, alias="i")
    assert from_.left.right == A.TableName(ORDERS, alias="o")
    assert from_.right == A.TableName(CUSTOMERS, alias="c")
    assert from_.left.on == A.Compare("=", A.Column(Ident("order_id"), "i"), A.Column(ID, "o"))
    assert where is not None and [p.value for p in A.params_of(where)] == ["a"]
    assert [p.value for p in A.params_of(from_.on)] == ["b"]
    names = [p.name for p in A.params_of(A.And((where, from_.on)))]  # type: ignore[arg-type]
    assert len(set(names)) == 2


def test_join_accepts_reversed_edge_and_composite_cast_edge() -> None:
    e1 = JoinEdge(
        (cref(ITEMS, "order_id"), cref(ITEMS, "shop_id")),
        (cref(ORDERS, "id"), cref(ORDERS, "shop_id")),
        cast_to="string",
    )
    e2 = EdgeRef(cref(ORDERS, "customer_id"), cref(CUSTOMERS, "id"))
    e3 = EdgeRef(cref(TableRef(None, None, Ident("events")), "order_id"), cref(ORDERS, "id"))
    from_, where = lower_join([e1, e2, e3])
    assert where is None
    casts = of(from_, A.Cast)
    assert len(casts) == 4
    tables = [n.ref for n in of(from_, A.TableName)]  # type: ignore[attr-defined]
    assert tables[-1].table.text == "events"
    assert {n.alias for n in of(from_, A.TableName)} == {"t1", "t2", "t3", "t4"}  # type: ignore[attr-defined]


def test_join_rejects_disconnected_or_revisiting_paths() -> None:
    e1 = EdgeRef(cref(ITEMS, "order_id"), cref(ORDERS, "id"))
    stray = EdgeRef(cref(CUSTOMERS, "x"), cref(TableRef(None, None, Ident("events")), "y"))
    with pytest.raises(ValueError, match="not connected"):
        lower_join([e1, stray])
    with pytest.raises(ValueError, match="revisits"):
        lower_join([e1, EdgeRef(cref(ITEMS, "x"), cref(ORDERS, "y"))])
    with pytest.raises(ValueError, match="not in the join path"):
        lower_join([e1], filters={CUSTOMERS: status_is()})
    with pytest.raises(ValueError):
        lower_join([])


def test_rendered_fragment_binds_and_inlines() -> None:
    frag = RenderedFragment(
        "o.status = {{p1}}", (Param("p1", HOSTILE, "string"),), "duckdb", "verified"
    )
    sql, values = frag.bind("qmark")
    assert sql == "o.status = ?" and values == (HOSTILE,)
    assert frag.inline() == "o.status = 'x''); DROP TABLE orders; --'"
