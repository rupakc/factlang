"""Execute lowered plans on DuckDB: the AST means what spec 8.1/18/19 say it means.

The printer below is a minimal ANSI/DuckDB spelling local to this test (Task 14 brings the
real profiles); it exists only to run the lowering's trees against an engine.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from factlang.ir import (
    ColumnAggregate,
    CompositeKeyAggregate,
    KeyOverlap,
    Metric,
    PatternCoverage,
    SampleSpec,
    ValueMembership,
)
from factlang.semantics.filters import FBool, FCompare, FIsNull, FNot, FOr, Param
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef
from factlang.sql import ast as A
from factlang.sql.fragment import lower_join
from factlang.sql.lower import lower
from factlang.sql.probe import RenderedProbe

duckdb = pytest.importorskip("duckdb")

_TYPES = {"string": "VARCHAR", "integer": "BIGINT", "decimal": "DECIMAL(38,9)"}


def _ident(i: Ident) -> str:
    return '"' + i.text.replace('"', '""') + '"' if i.quoted else i.text


def expr(x: object) -> str:
    if isinstance(x, A.Column):
        return (x.qualifier + "." if x.qualifier else "") + _ident(x.name)
    if isinstance(x, A.ParamRef):
        return "{{" + x.param.name + "}}"
    if isinstance(x, A.Literal):
        if x.value is None:
            return "NULL"
        return "'" + x.value + "'" if isinstance(x.value, str) else str(x.value)
    if isinstance(x, A.Agg):
        arg = "*" if x.arg is None else expr(x.arg)
        return f"{x.fn}({'DISTINCT ' if x.distinct else ''}{arg})"
    if isinstance(x, A.CountIf):
        return f"COALESCE(SUM(CASE WHEN {expr(x.cond)} THEN 1 ELSE 0 END), 0)"
    if isinstance(x, A.Lower):
        return f"LOWER({expr(x.e)})"
    if isinstance(x, A.Trim):
        return f"TRIM({expr(x.e)})"
    if isinstance(x, A.SplitPrefix):
        return f"SPLIT_PART({expr(x.e)}, {expr(x.separator)}, 1)"
    if isinstance(x, A.Like):
        return f"({expr(x.e)} LIKE {expr(x.pattern)})"
    if isinstance(x, A.Cast):
        return f"CAST({expr(x.e)} AS {_TYPES[x.family]})"
    if isinstance(x, A.Coalesce):
        return "COALESCE(" + ", ".join(map(expr, x.items)) + ")"
    if isinstance(x, A.Case):
        whens = " ".join(f"WHEN {expr(c)} THEN {expr(r)}" for c, r in x.whens)
        tail = f" ELSE {expr(x.else_)}" if x.else_ is not None else ""
        return f"CASE {whens}{tail} END"
    if isinstance(x, A.Now):
        return "CURRENT_TIMESTAMP"
    if isinstance(x, A.Compare):
        return f"({expr(x.left)} {'<>' if x.op == '!=' else x.op} {expr(x.right)})"
    if isinstance(x, (A.And, A.Or)):
        word = " AND " if isinstance(x, A.And) else " OR "
        return "(" + word.join(map(expr, x.items)) + ")"
    if isinstance(x, A.Not):
        return f"(NOT {expr(x.e)})"
    if isinstance(x, A.InList):
        return f"({expr(x.e)} {'NOT ' if x.negated else ''}IN ({', '.join(map(expr, x.items))}))"
    if isinstance(x, A.IsNull):
        return f"({expr(x.e)} IS {'NOT ' if x.negated else ''}NULL)"
    if isinstance(x, A.Between):
        return f"({expr(x.e)} BETWEEN {expr(x.lo)} AND {expr(x.hi)})"
    if isinstance(x, A.KeyHash):  # spec 19 keyhash ruling, duckdb row
        s = " || '|' || ".join([expr(x.seed), *map(expr, x.parts)])
        return f"(hash({s}) % 1000000)"
    raise TypeError(x)


def from_item(x: object) -> str:
    if isinstance(x, A.TableName):
        out = ".".join(_ident(p) for p in x.ref.parts()) + (" " + x.alias if x.alias else "")
        if x.sample is not None:
            pct = x.sample.fraction * 100
            out += f" TABLESAMPLE {pct}% ({x.sample.method}, {x.sample.seed})"
        return out
    if isinstance(x, A.SubqueryFrom):
        return f"({statement(x.select)}) {x.alias}"
    if isinstance(x, A.Join):
        on = f" ON {expr(x.on)}" if x.on is not None else ""
        return f"{from_item(x.left)} {x.kind} JOIN {from_item(x.right)}{on}"
    raise TypeError(x)


def statement(x: A.Select | A.UnionAll) -> str:
    if isinstance(x, A.UnionAll):  # branches with ORDER BY/LIMIT must be parenthesised
        return " UNION ALL ".join(f"({statement(s)})" for s in x.selects)
    out = "SELECT " + ", ".join(f"{expr(c.expr)} AS {c.alias}" for c in x.columns)
    out += " FROM " + from_item(x.from_)
    if x.where is not None:
        out += " WHERE " + expr(x.where)
    if x.group_by:
        out += " GROUP BY " + ", ".join(map(expr, x.group_by))
    if x.order_by:
        out += " ORDER BY " + ", ".join(
            expr(o.expr) + (" DESC" if o.descending else " ASC") for o in x.order_by
        )
    if x.limit is not None:
        out += f" LIMIT {x.limit}"
    return out


ORDERS = TableRef(None, None, Ident("orders"))
ITEMS = TableRef(None, None, Ident("items"))
PRICES = TableRef(None, None, Ident("prices"))
BIG = TableRef(None, None, Ident("big"))


def c(t: TableRef, name: str) -> ColumnRef:
    return ColumnRef(t, Ident(name))


@pytest.fixture(scope="module")
def con() -> Any:
    db = duckdb.connect()
    db.execute(
        """
        CREATE TABLE orders(id INT, shop_id INT, status VARCHAR, deleted BOOLEAN);
        INSERT INTO orders VALUES (1, 1, 'paid', NULL), (2, 1, 'open', FALSE),
          (3, 1, 'void', TRUE), (4, 2, 'paid', NULL), (4, 2, 'x: y', NULL),
          (NULL, 1, NULL, NULL), (5, NULL, 'refund: late', NULL);
        CREATE TABLE items(order_id INT, shop_id INT, n INT);
        INSERT INTO items VALUES (1, 1, 1), (1, 1, 2), (2, 1, 1), (9, 1, 1), (NULL, 1, 1),
          (4, 2, 1), (4, 2, 3), (4, NULL, 1);
        CREATE TABLE prices(id DECIMAL(10, 2));
        INSERT INTO prices SELECT CAST(i AS DECIMAL(10, 2)) FROM range(0, 5000) t(i);
        CREATE TABLE big(id BIGINT, v BIGINT);
        INSERT INTO big SELECT i, i % 7 FROM range(0, 20000) t(i);
        """
    )
    return db


def run(con: Any, plan: object) -> list[dict[str, object]]:
    tree = lower(plan)  # type: ignore[arg-type]
    probe = RenderedProbe(
        statement(tree),
        A.params_of(tree),
        plan.arity,
        plan,
        "duckdb",
        "test",  # type: ignore[attr-defined]
    )
    sql, values = probe.bind("qmark")
    cur = con.execute(sql, list(values))  # type: ignore[arg-type]
    cols = [d[0] for d in cur.description]
    assert tuple(cols) == plan.result_columns()  # type: ignore[attr-defined]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def one(con: Any, plan: object) -> dict[str, object]:
    (row,) = run(con, plan)
    return row


def test_column_aggregate_null_rules(con: Any) -> None:
    metrics = (
        Metric.ROW_COUNT,
        Metric.NON_NULL,
        Metric.DISTINCT_KEYS,
        Metric.MAX_PER_KEY,
        Metric.DUPLICATE_KEYS,
        Metric.SUM,
        Metric.MIN_VALUE,
    )
    row = one(con, ColumnAggregate(ORDERS, Ident("id"), metrics))
    assert row == {
        "row_count": 7,
        "non_null": 6,
        "distinct_keys": 5,
        "max_rows_per_key": 2,
        "duplicate_keys": 1,
        "sum_value": 19,
        "min_value": 1,
    }


def test_empty_input_counts_are_zero_and_extremes_null(con: Any) -> None:
    none = FCompare(Ident("status"), "=", Param("p1", "nope", "string"))
    row = one(
        con,
        ColumnAggregate(
            ORDERS,
            Ident("id"),
            (Metric.ROW_COUNT, Metric.MAX_PER_KEY, Metric.DUPLICATE_KEYS, Metric.SUM),
            filter=none,
        ),
    )
    assert row == {"row_count": 0, "max_rows_per_key": None, "duplicate_keys": 0, "sum_value": None}


def test_composite_key_groups_and_excludes_partial_keys(con: Any) -> None:
    metrics = (
        Metric.ROW_COUNT,
        Metric.NON_NULL,
        Metric.DISTINCT_KEYS,
        Metric.MAX_PER_KEY,
        Metric.DUPLICATE_KEYS,
    )
    row = one(con, CompositeKeyAggregate(ITEMS, (Ident("order_id"), Ident("shop_id")), metrics))
    assert row == {
        "row_count": 8,
        "non_null": 6,
        "distinct_keys": 4,
        "max_rows_per_key": 2,
        "duplicate_keys": 2,
    }


def test_value_membership_declared(con: Any) -> None:
    rows = run(con, ValueMembership(ORDERS, Ident("status"), ("paid", "open")))
    assert rows[0]["kind"] == "summary"
    assert (rows[0]["non_null"], rows[0]["declared_rows"], rows[0]["undeclared_distinct"]) == (
        6,
        3,
        3,
    )
    assert [(r["value"], r["freq"]) for r in rows[1:]] == [
        ("refund: late", 1),
        ("void", 1),
        ("x: y", 1),
    ]


def test_value_membership_empty_declared_is_exact_top_k(con: Any) -> None:
    rows = run(con, ValueMembership(BIG, Ident("v"), (), report_limit=4))
    reference = con.execute(
        "SELECT CAST(v AS VARCHAR) AS value, COUNT(*) AS freq FROM big WHERE v IS NOT NULL "
        "GROUP BY 1 ORDER BY freq DESC, value ASC LIMIT 4"
    ).fetchall()
    assert rows[0]["kind"] == "summary" and rows[0]["declared_rows"] == 0
    assert rows[0]["undeclared_distinct"] == 7
    assert [(r["value"], r["freq"]) for r in rows[1:]] == [tuple(r) for r in reference]


def test_value_membership_split_compares_trimmed_prefix(con: Any) -> None:
    rows = run(con, ValueMembership(ORDERS, Ident("status"), ("x", "refund"), split=":"))
    assert (rows[0]["declared_rows"], rows[0]["undeclared_distinct"]) == (2, 3)
    assert [(r["value"], r["freq"]) for r in rows[1:]] == [("paid", 2), ("open", 1), ("void", 1)]


def test_key_overlap_composite_with_multiplicity(con: Any) -> None:
    plan = KeyOverlap(
        (c(ITEMS, "order_id"), c(ITEMS, "shop_id")),
        (c(ORDERS, "id"), c(ORDERS, "shop_id")),
        measure_multiplicity=True,
    )
    assert one(con, plan) == {
        "distinct_left": 4,
        "matched": 3,
        "distinct_right": 4,
        "left_non_null": 6,
        "matched_left_rows": 5,
        "max_left_per_key": 2,
        "max_right_per_key": 2,
    }


def test_key_overlap_cast_filter_and_right_filter(con: Any) -> None:
    live = FOr((FIsNull(Ident("deleted")), FNot(FBool(Ident("deleted")))))
    plan = KeyOverlap(
        (c(ITEMS, "order_id"),),
        (c(ORDERS, "id"),),
        cast_to="string",
        filter=FCompare(Ident("n"), "=", Param("p1", 1, "integer")),
        right_filter=live,
    )
    assert one(con, plan) == {
        "distinct_left": 4,
        "matched": 3,
        "distinct_right": 4,
        "left_non_null": 5,
        "matched_left_rows": 4,
    }


def test_pattern_coverage_is_case_folded(con: Any) -> None:
    assert one(con, PatternCoverage(ORDERS, Ident("status"), "%A%")) == {
        "non_null": 6,
        "matching": 3,
    }


def test_keyhash_on_both_join_sides_keeps_identical_keys_after_cast(con: Any) -> None:
    # BIG.id is BIGINT, PRICES.id is DECIMAL(10,2): raw text differs ('4' vs '4.00'), so the
    # two sides agree only because the hashed key is the join key after CAST integer.
    lspec = SampleSpec("keyhash", Decimal("0.2"), seed=11, key=(Ident("id"),))
    plan = KeyOverlap(
        (c(BIG, "id"),),
        (c(PRICES, "id"),),
        cast_to="integer",
        sample=lspec,
        right_sample=lspec,
    )
    row = one(con, plan)
    exact = one(con, KeyOverlap((c(BIG, "id"),), (c(PRICES, "id"),), cast_to="integer"))
    assert exact["matched"] == 5000
    # every sampled right key is a sampled left key: the matched set is the whole right sample
    assert row["matched"] == row["distinct_right"]
    assert 0 < row["distinct_right"] < 5000  # type: ignore[operator]
    assert row["sampled_right_rows"] == row["distinct_right"]


def test_keyhash_same_seed_same_sample_and_fraction_one_is_exact(con: Any) -> None:
    spec = SampleSpec("keyhash", Decimal("0.1"), seed=3, key=(Ident("id"),))
    plan = ColumnAggregate(BIG, Ident("v"), (Metric.ROW_COUNT, Metric.SUM), sample=spec)
    first, second = one(con, plan), one(con, plan)
    assert first == second
    assert 0 < first["row_count"] < 20000  # type: ignore[operator]
    full = SampleSpec("keyhash", Decimal(1), seed=3, key=(Ident("id"),))
    exact = one(con, ColumnAggregate(BIG, Ident("v"), (Metric.ROW_COUNT, Metric.SUM)))
    sampled_full = one(
        con, ColumnAggregate(BIG, Ident("v"), (Metric.ROW_COUNT, Metric.SUM), sample=full)
    )
    assert sampled_full == {**exact, "sampled_rows": exact["row_count"]}


def test_keyhash_keeps_every_row_of_a_kept_key(con: Any) -> None:
    spec = SampleSpec("keyhash", Decimal("0.3"), seed=1, key=(Ident("v"),))
    rows = run(con, ValueMembership(BIG, Ident("v"), (), report_limit=7, sample=spec))
    full = {
        r[0]: r[1]
        for r in con.execute("SELECT CAST(v AS VARCHAR), COUNT(*) FROM big GROUP BY 1").fetchall()
    }
    for r in rows[1:]:
        assert r["freq"] == full[r["value"]]


def test_bernoulli_single_read_sample_runs(con: Any) -> None:
    spec = SampleSpec("bernoulli", Decimal("0.5"), seed=7)
    row = one(con, ColumnAggregate(BIG, Ident("v"), (Metric.ROW_COUNT,), sample=spec))
    assert row["row_count"] == row["sampled_rows"]
    assert 8000 < row["row_count"] < 12000  # type: ignore[operator]


def test_join_fragment_matches_a_hand_written_join(con: Any) -> None:
    paid = FCompare(Ident("status"), "=", Param("p1", "paid", "string"))
    edge = EdgeRef(c(ITEMS, "order_id"), c(ORDERS, "id"))
    from_, where = lower_join([edge], filters={ORDERS: paid})
    assert where is None
    on_params = A.params_of(from_)
    sql = "SELECT COUNT(*) FROM " + from_item(from_)
    for p in on_params:
        sql = sql.replace("{{" + p.name + "}}", "?")
    (got,) = con.execute(sql, [p.value for p in on_params]).fetchone()
    (want,) = con.execute(
        "SELECT COUNT(*) FROM items i JOIN orders o ON i.order_id = o.id AND o.status = 'paid'"
    ).fetchone()
    assert got == want == 5
