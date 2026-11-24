from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from factlang.ir import ColumnAggregate, CompositeKeyAggregate, Metric, SampleSpec, ValueMembership
from factlang.optimizer import FusedAggregate, WorkItem, optimize, split
from factlang.semantics.filters import FCompare, Param
from factlang.semantics.refs import Ident, TableRef
from factlang.sql import get_profile, render

T = TableRef(None, None, Ident("orders"))
U = TableRef(None, None, Ident("items"))
NULLS = (Metric.ROW_COUNT, Metric.NON_NULL)


def agg(col: str, table: TableRef = T, flt: Any = None, sample: Any = None, metrics: Any = NULLS):
    return ColumnAggregate(table, Ident(col), metrics, flt, sample)


def raw(plan: Any) -> WorkItem:
    return WorkItem(plan)


def test_two_columns_one_table_fuse() -> None:
    out = optimize([raw(agg("a")), raw(agg("b"))])
    assert len(out) == 1
    assert isinstance(out[0].plan, FusedAggregate)
    assert len(out[0].members) == 2


def test_different_filters_or_tables_do_not_fuse() -> None:
    f = FCompare(Ident("s"), "=", Param("p1", "x", "string"))
    assert len(optimize([raw(agg("a")), raw(agg("b", flt=f))])) == 2
    assert len(optimize([raw(agg("a")), raw(agg("b", table=U))])) == 2


def test_param_name_does_not_matter_for_filter() -> None:
    f1 = FCompare(Ident("s"), "=", Param("p1", "x", "string"))
    f2 = FCompare(Ident("s"), "=", Param("zz", "x", "string"))
    assert len(optimize([raw(agg("a", flt=f1)), raw(agg("b", flt=f2))])) == 1


def test_dedupe_by_key_and_fuse_false() -> None:
    out = optimize([raw(agg("a")), raw(agg("a")), raw(agg("b"))], fuse=False)
    assert len(out) == 2
    assert all(not isinstance(p.plan, FusedAggregate) for p in out)
    assert len(optimize([WorkItem(agg("a"), key="k"), WorkItem(agg("b"), key="k")])) == 1


def test_samples_never_mix() -> None:
    s1 = SampleSpec("keyhash", Decimal("0.5"), 1, (Ident("id"),))
    s2 = SampleSpec("keyhash", Decimal("0.5"), 2, (Ident("id"),))
    assert len(optimize([raw(agg("a", sample=s1)), raw(agg("b", sample=s2))])) == 2
    assert len(optimize([raw(agg("a", sample=s1)), raw(agg("b"))])) == 2
    assert len(optimize([raw(agg("a", sample=s1)), raw(agg("b", sample=s1))])) == 1


def test_multi_read_plans_stay_unfused() -> None:
    sysm = SampleSpec("system", Decimal("0.5"))
    keyed = agg("a", sample=sysm, metrics=(Metric.MAX_PER_KEY,))
    out = optimize([raw(keyed), raw(agg("b", sample=sysm)), raw(agg("c", sample=sysm))])
    assert len(out) == 2
    assert out[0].plan == keyed
    assert isinstance(out[1].plan, FusedAggregate)  # single-read system sample fuses fine
    assert len(optimize([raw(agg("a", metrics=(Metric.DUPLICATE_KEYS,))), raw(agg("b"))])) == 2
    vm = ValueMembership(T, Ident("a"), ("x",))
    assert len(optimize([raw(vm), raw(agg("b"))])) == 2


def test_composite_fuses_only_with_identical_columns() -> None:
    def comp(*cols: str) -> CompositeKeyAggregate:
        return CompositeKeyAggregate(T, tuple(Ident(c) for c in cols), NULLS)

    assert len(optimize([raw(comp("a", "b")), raw(comp("a", "b"))])) == 1  # deduped
    assert len(optimize([raw(comp("a", "b")), raw(comp("a", "c"))])) == 2
    assert len(optimize([raw(comp("a", "b")), raw(agg("a"))])) == 2


def test_split_restores_exact_aliases_including_sampled_rows() -> None:
    s = SampleSpec("keyhash", Decimal("0.5"), 1, (Ident("id"),))
    p1, p2 = agg("a", sample=s), agg("b", sample=s)
    (fp,) = optimize([raw(p1), raw(p2)])
    assert fp.plan.result_columns()[0] == "m0__row_count"  # type: ignore[attr-defined]
    row = dict(zip(fp.plan.result_columns(), range(10), strict=False))  # type: ignore[attr-defined]
    got = split(fp, [row])
    k1, k2 = (k for k, _ in fp.members)
    assert tuple(got[k1][0]) == p1.result_columns()
    assert tuple(got[k2][0]) == p2.result_columns()
    assert "sampled_rows" in got[k1][0]


def test_claim_hash_is_the_key() -> None:
    class C:
        semantic_hash = "h1"

    items = [WorkItem(agg("a"), C()), WorkItem(agg("b"), C())]  # type: ignore[arg-type]
    assert len(optimize(items)) == 1


duckdb = pytest.importorskip("duckdb")


def _run(con: Any, plan: Any) -> list[dict[str, object]]:
    duck = get_profile("duckdb")
    assert duck is not None
    sql, values = render(plan, duck).bind("qmark")
    cur = con.execute(sql, list(values))
    cols = [d[0] for d in cur.description]
    assert tuple(cols) == plan.result_columns()
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


@pytest.mark.parametrize("sampled", [False, True])
def test_fused_equals_unfused_on_duckdb(sampled: bool) -> None:
    con = duckdb.connect()
    con.execute(
        "CREATE TABLE orders(id INT, a VARCHAR, b INT, c INT);"
        "INSERT INTO orders SELECT i, CASE WHEN i % 3 = 0 THEN NULL ELSE 'x' END, "
        "CASE WHEN i % 5 = 0 THEN NULL ELSE i END, i % 7 FROM range(1, 3000) t(i);"
    )
    s = SampleSpec("keyhash", Decimal("0.3"), 4, (Ident("id"),)) if sampled else None
    f = FCompare(Ident("c"), ">", Param("p1", 1, "integer"))
    plans = [
        agg("a", sample=s, metrics=(Metric.ROW_COUNT, Metric.NON_NULL, Metric.DISTINCT_KEYS)),
        agg("b", sample=s, metrics=(Metric.NON_NULL, Metric.SUM, Metric.MAX_VALUE)),
        agg("c", sample=s, metrics=(Metric.ROW_COUNT, Metric.MIN_VALUE)),
        agg("c", sample=s, flt=f, metrics=(Metric.ROW_COUNT,)),
        agg("a", sample=s, flt=f, metrics=(Metric.NON_NULL,)),
    ]
    items = [WorkItem(p, key=f"k{i}") for i, p in enumerate(plans)]
    fused = optimize(items)
    assert len(fused) == 2
    for fp in fused:
        rows = _run(con, fp.plan)
        got = split(fp, rows)
        for key, _ in fp.members:
            plan = plans[int(key[1:])]
            assert got[key] == _run(con, plan)


def test_quoted_and_bare_table_never_fuse() -> None:
    quoted = TableRef(None, None, Ident("Orders", quoted=True))
    bare = TableRef(None, None, Ident("Orders"))
    out = optimize([raw(agg("a", table=quoted)), raw(agg("b", table=bare))])
    assert len(out) == 2
    for p in out:
        render(p.plan, get_profile("duckdb"))  # each renders, never FL500
