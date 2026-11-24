"""Execute every primitive through the real DuckDB profile (spec 8.5, 18 R6, 19 B4 (a)-(e))."""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

import pytest

from factlang.ir import (
    ColumnAggregate,
    CompositeKeyAggregate,
    KeyOverlap,
    MetadataQuery,
    Metric,
    PatternCoverage,
    SampleSpec,
    ValueMembership,
)
from factlang.semantics.filters import FBool, FCompare, FIsNull, FNot, FOr, Param
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef
from factlang.sql import RenderError, get_profile, render, render_filter, render_join

duckdb = pytest.importorskip("duckdb")

DUCK = get_profile("duckdb")
assert DUCK is not None

ORDERS = TableRef(None, None, Ident("orders"))
ITEMS = TableRef(None, None, Ident("order_items"))
LEFT = TableRef(None, None, Ident("events"))
RIGHT = TableRef(None, Ident("main"), Ident("customers"))
BIG = TableRef(None, None, Ident("big"))
ID, V = Ident("id"), Ident("v")
N_BIG = 1_000_000


def c(t: TableRef, name: str) -> ColumnRef:
    return ColumnRef(t, Ident(name))


@pytest.fixture(scope="module")
def con() -> Any:
    db = duckdb.connect()
    db.execute(
        """
        CREATE TABLE orders(id INT, customer_id INT, status VARCHAR, "Order Total" DECIMAL(10,2),
                            deleted BOOLEAN, created_at TIMESTAMP);
        INSERT INTO orders VALUES
          (1, 10, 'paid', 5.00, NULL, '2026-01-01'), (2, 10, 'open', 7.50, FALSE, '2026-01-02'),
          (3, 11, 'void', NULL, TRUE, '2026-01-03'), (4, 12, 'Paid', 1.25, NULL, NULL),
          (4, 12, 'x: y', 3.00, NULL, NULL), (NULL, NULL, NULL, NULL, NULL, NULL);
        CREATE TABLE order_items(order_id INT, line INT, n INT);
        INSERT INTO order_items VALUES (1, 1, 1), (1, 2, 2), (2, 1, 1), (9, 1, 1), (NULL, 1, 1),
          (4, 1, 3), (4, 1, 1);
        CREATE TABLE events(id BIGINT, kind VARCHAR);
        INSERT INTO events SELECT i % 5000, 'e' || (i % 3) FROM range(0, 15000) t(i);
        CREATE TABLE customers(id BIGINT, name VARCHAR);
        INSERT INTO customers SELECT i, 'c' || i FROM range(0, 5000) t(i);
        CREATE TABLE big(id BIGINT, v BIGINT);
        INSERT INTO big SELECT i, i % 7 FROM range(0, 1000000) t(i);
        """
    )
    return db


def run(con: Any, plan: object) -> list[dict[str, object]]:
    probe = render(plan, DUCK)  # type: ignore[arg-type]
    sql, values = probe.bind("qmark")
    cur = con.execute(sql, list(values))  # type: ignore[arg-type]
    cols = [d[0] for d in cur.description]
    assert tuple(cols) == plan.result_columns()  # type: ignore[attr-defined]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def one(con: Any, plan: object) -> dict[str, object]:
    (row,) = run(con, plan)
    return row


# -- every primitive, exact -------------------------------------------------------------------


def test_column_aggregate_every_metric(con: Any) -> None:
    row = one(con, ColumnAggregate(ORDERS, ID, tuple(m for m in Metric if m is not Metric.NOW)))
    assert row == {
        "row_count": 6,
        "non_null": 5,
        "distinct_keys": 4,
        "max_rows_per_key": 2,
        "min_value": 1,
        "max_value": 4,
        "sum_value": 14,
        "avg_value": 2.8,
        "duplicate_keys": 1,
    }
    # server_now is a TIMESTAMP WITH TIME ZONE: fetching it into Python needs pytz/tzdata,
    # which the test environment need not have, so the probe runs inside a wrapper
    probe = render(ColumnAggregate(ORDERS, None, (Metric.NOW, Metric.ROW_COUNT)), DUCK)
    sql, values = probe.bind("qmark")
    wrapped = f"SELECT server_now IS NOT NULL, row_count FROM ({sql}) q"
    assert con.execute(wrapped, list(values)).fetchone() == (True, 6)


def test_quoted_identifier_with_a_space(con: Any) -> None:
    total = Ident("Order Total", quoted=True)
    assert one(con, ColumnAggregate(ORDERS, total, (Metric.SUM,))) == {
        "sum_value": Decimal("16.75")
    }


def test_filters_execute(con: Any) -> None:
    live = FOr((FIsNull(Ident("deleted")), FNot(FBool(Ident("deleted")))))
    assert one(con, ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,), filter=live)) == {
        "row_count": 5
    }


def test_composite_key_aggregate(con: Any) -> None:
    metrics = (
        Metric.ROW_COUNT,
        Metric.NON_NULL,
        Metric.DISTINCT_KEYS,
        Metric.MAX_PER_KEY,
        Metric.DUPLICATE_KEYS,
    )
    row = one(con, CompositeKeyAggregate(ITEMS, (Ident("order_id"), Ident("line")), metrics))
    assert row == {
        "row_count": 7,
        "non_null": 6,
        "distinct_keys": 5,
        "max_rows_per_key": 2,
        "duplicate_keys": 1,
    }


def test_count_if_is_zero_on_no_rows(con: Any) -> None:
    none = FCompare(Ident("status"), "=", Param("p1", "nope", "string"))
    row = one(con, PatternCoverage(ORDERS, Ident("status"), "%a%", filter=none))
    assert row == {"non_null": 0, "matching": 0}
    none_n = FCompare(Ident("n"), "=", Param("p1", 99, "integer"))
    keys = (Ident("order_id"), Ident("line"))
    row = one(
        con,
        CompositeKeyAggregate(ITEMS, keys, (Metric.NON_NULL, Metric.DUPLICATE_KEYS), filter=none_n),
    )
    assert row == {"non_null": 0, "duplicate_keys": 0}


def test_value_membership_declared_and_split(con: Any) -> None:
    rows = run(con, ValueMembership(ORDERS, Ident("status"), ("paid", "open")))
    assert rows[0] == {
        "kind": "summary",
        "non_null": 5,
        "declared_rows": 2,
        "undeclared_distinct": 3,
        "value": None,
        "freq": None,
    }
    assert [(r["value"], r["freq"]) for r in rows[1:]] == [("Paid", 1), ("void", 1), ("x: y", 1)]
    split = run(con, ValueMembership(ORDERS, Ident("status"), ("x",), split=":"))
    assert split[0]["declared_rows"] == 1


def test_value_membership_empty_declared_is_exact_top_k(con: Any) -> None:
    rows = run(con, ValueMembership(LEFT, Ident("kind"), (), report_limit=2))
    assert rows[0]["undeclared_distinct"] == 3
    assert [(r["value"], r["freq"]) for r in rows[1:]] == [("e0", 5000), ("e1", 5000)]


def test_key_overlap(con: Any) -> None:
    plan = KeyOverlap(
        (c(ITEMS, "order_id"),), (c(ORDERS, "id"),), cast_to="string", measure_multiplicity=True
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


def test_pattern_coverage(con: Any) -> None:
    assert one(con, PatternCoverage(ORDERS, Ident("status"), "%PAID%")) == {
        "non_null": 5,
        "matching": 2,
    }


def test_quoted_name_matches_the_catalog_as_sql_resolves_it(con: Any) -> None:
    # DuckDB resolves even a quoted identifier case-insensitively: "ID" is column id, so the
    # catalog lookup must find it too, consistent with ColumnAggregate
    quoted = Ident("ID", quoted=True)
    assert one(con, ColumnAggregate(ORDERS, quoted, (Metric.NON_NULL,))) == {"non_null": 5}
    assert run(con, MetadataQuery(ORDERS, quoted)) == [
        {"column_name": "id", "data_type": "INTEGER"}
    ]
    assert len(run(con, MetadataQuery(TableRef(None, None, Ident("Orders", quoted=True))))) == 6


def test_metadata_query(con: Any) -> None:
    rows = run(con, MetadataQuery(ORDERS))
    assert {r["column_name"]: r["data_type"] for r in rows}["Order Total"] == "DECIMAL(10,2)"
    assert len(rows) == 6
    upper = TableRef(None, None, Ident("ORDERS"))  # unquoted: DuckDB resolves it as `orders`
    assert len(run(con, MetadataQuery(upper, Ident("STATUS")))) == 1
    assert run(con, MetadataQuery(RIGHT, Ident("name"))) == [
        {"column_name": "name", "data_type": "VARCHAR"}
    ]


# -- fragments (R6) ---------------------------------------------------------------------------


def test_render_join_matches_a_hand_written_join(con: Any) -> None:
    paid = FCompare(Ident("status"), "=", Param("p1", "paid", "string"))
    one_item = FCompare(Ident("n"), "=", Param("p1", 1, "integer"))
    frag = render_join(
        [EdgeRef(c(ITEMS, "order_id"), c(ORDERS, "id"))],
        "duckdb",
        filters={ORDERS: paid, ITEMS: one_item},
    )
    sql, values = frag.bind("qmark")
    (got,) = con.execute("SELECT COUNT(*) " + sql, list(values)).fetchone()
    (want,) = con.execute(
        "SELECT COUNT(*) FROM order_items i JOIN orders o ON i.order_id = o.id "
        "AND o.status = 'paid' WHERE i.n = 1"
    ).fetchone()
    assert got == want == 1


def test_render_filter_executes_in_a_where(con: Any) -> None:
    live = FOr((FIsNull(Ident("deleted")), FNot(FBool(Ident("deleted")))))
    frag = render_filter(live, table=ORDERS, dialect="duckdb", alias="o")
    sql, values = frag.bind("qmark")
    (got,) = con.execute("SELECT COUNT(*) FROM orders o WHERE " + sql, list(values)).fetchone()
    assert got == 5


# -- sampling, spec 19 B4 ----------------------------------------------------------------------


def keyhash(fraction: str, seed: int = 0, key: tuple[Ident, ...] = (ID,)) -> SampleSpec:
    return SampleSpec("keyhash", Decimal(fraction), seed=seed, key=key)


def test_a_keyhash_on_both_join_sides_keeps_identical_key_sets(con: Any) -> None:
    spec = keyhash("0.2", seed=11)
    plan = KeyOverlap((c(LEFT, "id"),), (c(RIGHT, "id"),), sample=spec, right_sample=spec)
    row = one(con, plan)
    # matched == |L| == |R| <=> the two sampled key sets are identical
    assert row["matched"] == row["distinct_left"] == row["distinct_right"]
    assert 600 < row["distinct_left"] < 1400  # type: ignore[operator]
    assert row["sampled_left_rows"] == 3 * row["distinct_left"]  # type: ignore[operator]
    exact = one(con, KeyOverlap((c(LEFT, "id"),), (c(RIGHT, "id"),)))
    assert exact["matched"] == 5000


def test_a_keyhash_keeps_identical_keys_across_cast(con: Any) -> None:
    con.execute(
        "CREATE OR REPLACE TABLE prices AS SELECT CAST(i AS DECIMAL(10,2)) id FROM range(5000) t(i)"
    )
    prices = TableRef(None, None, Ident("prices"))
    spec = keyhash("0.3", seed=5)
    plan = KeyOverlap(
        (c(prices, "id"),), (c(RIGHT, "id"),), cast_to="integer", sample=spec, right_sample=spec
    )
    row = one(con, plan)
    assert row["matched"] == row["distinct_left"] == row["distinct_right"]


def test_b_keyhash_keeps_every_row_of_a_kept_key(con: Any) -> None:
    spec = keyhash("0.3", seed=1, key=(V,))
    rows = run(con, ValueMembership(BIG, V, (), report_limit=7, sample=spec))
    assert rows[1:], "the sample kept at least one key"
    for r in rows[1:]:
        assert r["freq"] in (142857, 142858)  # every row of each kept key
    agg = one(con, CompositeKeyAggregate(LEFT, (ID,), (Metric.MAX_PER_KEY,), sample=keyhash("0.1")))
    assert agg["max_rows_per_key"] == 3


def _spec(method: str, seed: int) -> SampleSpec:
    if method == "keyhash":
        return keyhash("0.01", seed)
    return SampleSpec(method, Decimal("0.01"), seed=seed)  # type: ignore[arg-type]


DECLARED = [m for m in ("keyhash", "system", "bernoulli") if DUCK.supports_sample(m)]


def test_duckdb_declares_only_repeatable_methods() -> None:
    assert DECLARED == ["keyhash", "bernoulli"]  # SYSTEM is not repeatable across threads


@pytest.mark.parametrize("method", DECLARED)
def test_c_same_seed_same_sample_other_seed_other_sample(con: Any, method: str) -> None:
    # every method the profile declares must repeat exactly; rerun several times because a
    # non-repeatable (thread-dependent) sample differs only some of the time
    def sample(seed: int) -> dict[str, object]:
        plan = ColumnAggregate(BIG, ID, (Metric.SUM, Metric.ROW_COUNT), sample=_spec(method, seed))
        return one(con, plan)

    first = sample(3)
    assert all(sample(3) == first for _ in range(8))
    assert sample(4) != first


def test_system_sample_is_fl506_on_duckdb() -> None:
    plan = ColumnAggregate(
        BIG, None, (Metric.ROW_COUNT,), sample=SampleSpec("system", Decimal("0.3"))
    )
    with pytest.raises(RenderError, match="FL506"):
        render(plan, DUCK)


def test_d_fraction_one_is_identical_to_exact(con: Any) -> None:
    metrics = (Metric.ROW_COUNT, Metric.SUM, Metric.DISTINCT_KEYS)
    exact = one(con, ColumnAggregate(BIG, V, metrics))
    for spec in (
        keyhash("1"),
        SampleSpec("system", Decimal(1)),
        SampleSpec("bernoulli", Decimal(1), seed=2),
    ):
        got = one(con, ColumnAggregate(BIG, V, metrics, sample=spec))
        assert got == {**exact, "sampled_rows": N_BIG}


@pytest.mark.parametrize("method", [m for m in DECLARED if m != "keyhash"])
def test_e_block_and_row_samples_within_binomial_tolerance(con: Any, method: str) -> None:
    block = 1  # bernoulli: one trial per row (DuckDB declares no SYSTEM style)
    fraction = Decimal("0.3")
    spec = SampleSpec(method, fraction, seed=17)  # type: ignore[arg-type]
    row = one(con, ColumnAggregate(BIG, None, (Metric.ROW_COUNT,), sample=spec))
    assert row["row_count"] == row["sampled_rows"]
    units = N_BIG / block
    p = float(fraction)
    sd_rows = math.sqrt(units * p * (1 - p)) * block
    assert abs(row["row_count"] - N_BIG * p) <= 6 * sd_rows  # type: ignore[operator]
