"""The sqlglot bridge (spec 8.3, 8.4, 8.5, 10, 19 B2): long-tail dialects, tier best_effort.

The bridge prints the same internal SQL AST as the native printer, so on DuckDB both paths
must return identical rows for every primitive (the cross-check forces the bridge with
``renderer_for(..., prefer="sqlglot")``).
"""

from __future__ import annotations

import dataclasses
import importlib
import re
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
from factlang.semantics.filters import FBool, FCompare, FIn, FIsNull, FNot, FOr, Param
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef
from factlang.sql import (
    SQLGLOT_NAMES,
    RenderError,
    get_profile,
    native_dialects,
    render,
    render_filter,
    render_join,
    renderer_for,
)
from factlang.sql import ast as A

sqlglot = pytest.importorskip("sqlglot")
bridge = importlib.import_module("factlang.sql.sqlglot_bridge")  # sqlglot present: must import

DUCK = get_profile("duckdb")
assert DUCK is not None

ORDERS = TableRef(None, None, Ident("orders"))
ITEMS = TableRef(None, None, Ident("order_items"))
EVENTS = TableRef(None, None, Ident("events"))
CUSTOMERS = TableRef(None, Ident("main"), Ident("customers"))
BIG = TableRef(None, None, Ident("big"))
ID, V = Ident("id"), Ident("v")

# sqlglot dialects with no native FactLang profile (trino is native per Amendment A, R7)
LONG_TAIL = ("presto", "athena", "hive", "teradata", "starrocks", "doris", "spark2")
_TOKEN = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_]*)\}\}")


def c(t: TableRef, name: str) -> ColumnRef:
    return ColumnRef(t, Ident(name))


def keyhash(fraction: str, seed: int = 0, key: tuple[Ident, ...] = (ID,)) -> SampleSpec:
    return SampleSpec("keyhash", Decimal(fraction), seed=seed, key=key)


LIVE = FOr((FIsNull(Ident("deleted")), FNot(FBool(Ident("deleted")))))
PAID = FIn(Ident("status"), (Param("p1", "paid", "string"), Param("p2", "open", "string")))
NO_NOW = tuple(m for m in Metric if m is not Metric.NOW)
KEYED = (
    Metric.ROW_COUNT,
    Metric.NON_NULL,
    Metric.DISTINCT_KEYS,
    Metric.MAX_PER_KEY,
    Metric.DUPLICATE_KEYS,
)

# every primitive, exact
EXACT: dict[str, object] = {
    "column_aggregate": ColumnAggregate(ORDERS, ID, NO_NOW),
    "column_aggregate_filtered": ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,), filter=LIVE),
    "column_aggregate_in": ColumnAggregate(ORDERS, ID, (Metric.NON_NULL,), filter=PAID),
    "quoted_column": ColumnAggregate(ORDERS, Ident("Order Total", quoted=True), (Metric.SUM,)),
    "composite": CompositeKeyAggregate(ITEMS, (Ident("order_id"), Ident("line")), KEYED),
    "membership": ValueMembership(ORDERS, Ident("status"), ("paid", "open")),
    "membership_split": ValueMembership(ORDERS, Ident("status"), ("x",), split=":"),
    "membership_top_k": ValueMembership(EVENTS, Ident("kind"), (), report_limit=2),
    "overlap": KeyOverlap(
        (c(ITEMS, "order_id"),), (c(ORDERS, "id"),), cast_to="string", measure_multiplicity=True
    ),
    "overlap_composite": KeyOverlap(
        (c(ITEMS, "order_id"), c(ITEMS, "line")),
        (c(ORDERS, "id"), c(ORDERS, "customer_id")),
        right_filter=LIVE,
    ),
    "pattern": PatternCoverage(ORDERS, Ident("status"), "%PAID%"),
    "metadata": MetadataQuery(ORDERS),
    "metadata_column": MetadataQuery(CUSTOMERS, Ident("name")),
}
# sampled plans DuckDB can express: keyhash everywhere, bernoulli on single-read plans
SAMPLED: dict[str, object] = {
    "keyhash_overlap": KeyOverlap(
        (c(EVENTS, "id"),),
        (c(CUSTOMERS, "id"),),
        sample=keyhash("0.2", 11),
        right_sample=keyhash("0.2", 11),
    ),
    "keyhash_membership": ValueMembership(
        BIG, V, (), report_limit=7, sample=keyhash("0.3", 1, (V,))
    ),
    "keyhash_composite": CompositeKeyAggregate(
        EVENTS, (ID,), (Metric.MAX_PER_KEY,), sample=keyhash("0.1")
    ),
    "keyhash_aggregate": ColumnAggregate(
        BIG, ID, (Metric.SUM, Metric.ROW_COUNT), sample=keyhash("0.01", 3)
    ),
    "keyhash_pattern": PatternCoverage(
        EVENTS, Ident("kind"), "e1", sample=keyhash("0.5", 2, (ID,))
    ),
    "bernoulli_aggregate": ColumnAggregate(
        BIG, ID, (Metric.SUM, Metric.ROW_COUNT), sample=SampleSpec("bernoulli", Decimal("0.05"), 7)
    ),
}


@pytest.fixture(scope="module")
def con() -> Any:
    duckdb = pytest.importorskip("duckdb")
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


def execute(con: Any, probe: Any) -> list[tuple[object, ...]]:
    sql, values = probe.bind("qmark")
    cur = con.execute(sql, list(values))
    assert tuple(d[0] for d in cur.description) == probe.plan.result_columns()
    rows: list[tuple[object, ...]] = cur.fetchall()
    return rows


# -- cross-check: native and bridged DuckDB SQL return identical rows ---------------------


@pytest.mark.parametrize("name", sorted(EXACT) + sorted(SAMPLED))
def test_bridge_and_native_return_identical_rows_on_duckdb(con: Any, name: str) -> None:
    plan = {**EXACT, **SAMPLED}[name]
    native = render(plan, DUCK)  # type: ignore[arg-type]
    bridged = renderer_for("duckdb", prefer="sqlglot")(plan)  # type: ignore[arg-type]
    assert bridged.tier == "best_effort" and bridged.dialect == "duckdb"
    assert native.tier == "verified"
    want, got = execute(con, native), execute(con, bridged)
    if isinstance(plan, MetadataQuery):  # the catalog has no row order
        want, got = sorted(want, key=repr), sorted(got, key=repr)
    assert got == want
    assert got, "the cross-check compares something"


def test_render_sqlglot_is_the_registry_hook(con: Any) -> None:
    plan = EXACT["column_aggregate"]
    a = bridge.render_sqlglot(plan, "duckdb")
    b = bridge.render(plan, "duckdb")
    assert a == b
    assert execute(con, a) == execute(con, render(plan, DUCK))  # type: ignore[arg-type]


def test_server_now_through_the_bridge(con: Any) -> None:
    probe = bridge.render_sqlglot(
        ColumnAggregate(ORDERS, None, (Metric.NOW, Metric.ROW_COUNT)), "duckdb"
    )
    sql, values = probe.bind("qmark")
    wrapped = f"SELECT server_now IS NOT NULL, row_count FROM ({sql}) q"
    assert con.execute(wrapped, list(values)).fetchone() == (True, 6)


def test_bridged_keyhash_keeps_identical_key_sets_on_duckdb(con: Any) -> None:
    (row,) = execute(con, bridge.render_sqlglot(SAMPLED["keyhash_overlap"], "duckdb"))
    distinct_left, matched, distinct_right = row[0], row[1], row[2]
    assert matched == distinct_left == distinct_right
    assert 600 < distinct_left < 1400  # type: ignore[operator]


# -- long-tail dialects: render, bind, parse back with sqlglot ---------------------------------


@pytest.mark.parametrize("dialect", LONG_TAIL)
@pytest.mark.parametrize("name", sorted(EXACT))
def test_long_tail_dialects_render_bind_and_parse_back(dialect: str, name: str) -> None:
    assert get_profile(dialect) is None, f"{dialect} is native now; pick another long-tail one"
    plan = EXACT[name]
    probe = renderer_for(dialect)(plan)  # type: ignore[arg-type]  # non-native fallback
    assert probe.tier == "best_effort" and probe.dialect == dialect
    assert probe.sql_template.startswith(("SELECT ", "WITH ")) and ";" not in probe.sql_template
    # the neutral tokens survived generation: exactly the probe's parameters
    assert {m.group(1) for m in _TOKEN.finditer(probe.sql_template)} == {
        p.name for p in probe.params
    }
    for style in ("qmark", "named", "numeric", "format", "pyformat"):
        sql, _ = probe.bind(style)
        assert "{{" not in sql
    sql, values = probe.bind("qmark")
    assert sql.count("?") == len(values)  # type: ignore[arg-type]
    tree = sqlglot.parse_one(sql, read=dialect)
    assert isinstance(tree, sqlglot.exp.Query)


@pytest.mark.parametrize("dialect", [*LONG_TAIL, "trino", "exasol", "dremio"])
def test_every_parameter_is_a_bind_parameter(dialect: str) -> None:
    if not bridge.supports_dialect(dialect):
        pytest.skip(f"sqlglot {sqlglot.__version__} has no {dialect} dialect")
    secret = "o'brien; DROP TABLE x"
    f = FCompare(Ident("status"), "=", Param("p1", secret, "string"))
    probe = bridge.render_sqlglot(
        ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,), filter=f), dialect
    )
    assert secret not in probe.sql_template and "brien" not in probe.sql_template
    assert [p.value for p in probe.params] == [secret]


def test_params_are_in_first_use_order() -> None:
    probe = bridge.render_sqlglot(EXACT["membership_split"], "presto")
    names = [m.group(1) for m in _TOKEN.finditer(probe.sql_template)]
    first_use = list(dict.fromkeys(names))
    assert [p.name for p in probe.params] == first_use


def test_value_membership_limit_is_spelled_by_the_dialect() -> None:
    plan = ValueMembership(ORDERS, Ident("status"), ("paid",), report_limit=5)
    assert " LIMIT 5" in bridge.render_sqlglot(plan, "presto").sql_template
    assert "TOP 5 " in bridge.render_sqlglot(plan, "teradata").sql_template
    exact = bridge.render_sqlglot(ColumnAggregate(ORDERS, ID, NO_NOW), "presto").sql_template
    assert "LIMIT" not in exact and "TABLESAMPLE" not in exact.upper()


# -- identifiers (spec 8.4) ---------------------------------------------------------------------


def test_identifiers_follow_the_governing_rule() -> None:
    # unquoted -> unquoted, case as written
    sql = bridge.render_sqlglot(
        ColumnAggregate(ORDERS, Ident("CustId"), (Metric.NON_NULL,)), "presto"
    )
    assert "COUNT(CustId)" in sql.sql_template
    # quoted -> quoted with the dialect's quote character, inner quote doubled, case kept
    weird = Ident('Or"der', quoted=True)
    assert (
        '"Or""der"'
        in bridge.render_sqlglot(
            ColumnAggregate(ORDERS, weird, (Metric.NON_NULL,)), "presto"
        ).sql_template
    )
    tick = Ident("Or`der", quoted=True)
    assert (
        "`Or``der`"
        in bridge.render_sqlglot(
            ColumnAggregate(ORDERS, tick, (Metric.NON_NULL,)), "hive"
        ).sql_template
    )
    # an unquoted reserved word is quoted (after the dialect's case fold)
    reserved = bridge.render_sqlglot(
        ColumnAggregate(ORDERS, Ident("select"), (Metric.NON_NULL,)), "presto"
    )
    assert 'COUNT("select")' in reserved.sql_template
    parsed = bridge.render_sqlglot(
        ColumnAggregate(ORDERS, Ident("order"), (Metric.NON_NULL,)), "hive"
    )
    sqlglot.parse_one(parsed.bind("qmark")[0], read="hive")


@pytest.mark.parametrize(
    "bad",
    [
        Ident("a\x00b", quoted=True),
        Ident("a\nb", quoted=True),
        Ident("a;b", quoted=True),
        Ident("a{{b", quoted=True),
        Ident("x" * 129, quoted=True),
        Ident("a b"),  # "unquoted" but not a plain name
        Ident("", quoted=True),
    ],
)
def test_bad_identifiers_are_fl502(bad: Ident) -> None:
    with pytest.raises(RenderError, match="FL502"):
        bridge.render_sqlglot(ColumnAggregate(ORDERS, bad, (Metric.NON_NULL,)), "presto")


def test_identifier_limits_come_from_the_native_profile_when_there_is_one() -> None:
    long_ok = Ident("x" * 200)  # duckdb allows 1024 bytes; the long-tail default is 128
    bridge.render_sqlglot(ColumnAggregate(ORDERS, long_ok, (Metric.NON_NULL,)), "duckdb")
    with pytest.raises(RenderError, match="FL502"):
        bridge.render_sqlglot(ColumnAggregate(ORDERS, long_ok, (Metric.NON_NULL,)), "presto")


def test_table_parts_over_the_limit_are_fl505() -> None:
    three = TableRef(Ident("cat"), Ident("sch"), Ident("orders"))
    plan = ColumnAggregate(three, None, (Metric.ROW_COUNT,))
    assert "cat.sch.orders" in bridge.render_sqlglot(plan, "presto").sql_template
    with pytest.raises(RenderError, match="FL505"):
        bridge.render_sqlglot(plan, "presto", max_table_parts=2)
    with pytest.raises(RenderError, match="FL505"):
        bridge.render_sqlglot(MetadataQuery(three), "presto", max_table_parts=2)
    with pytest.raises(ValueError, match="max_table_parts"):
        bridge.render_sqlglot(plan, "presto", max_table_parts=0)


def test_unknown_dialect_is_fl504() -> None:
    with pytest.raises(RenderError, match="FL504") as err:
        bridge.render_sqlglot(EXACT["pattern"], "no_such_dialect")
    msg = str(err.value)
    assert "duckdb" in msg and "postgres" in msg and "pip install factlang[sqlglot]" in msg
    with pytest.raises(RenderError, match="FL504"):
        renderer_for("no_such_dialect")(EXACT["pattern"])  # type: ignore[arg-type]


def test_the_select_only_guard_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "_generate", lambda tree, d: "DELETE FROM orders")
    with pytest.raises(RenderError, match="SELECT-only guard"):
        bridge.render_sqlglot(EXACT["pattern"], "presto")
    monkeypatch.setattr(bridge, "_generate", lambda tree, d: "SELECT 1; SELECT 2")
    with pytest.raises(RenderError, match="SELECT-only guard"):
        bridge.render_sqlglot(EXACT["pattern"], "presto")


def test_a_dropped_placeholder_is_fl500(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bridge, "_generate", lambda tree, d: "SELECT COUNT(*) AS non_null FROM t")
    with pytest.raises(RenderError, match="FL500"):
        bridge.render_sqlglot(EXACT["pattern"], "presto")


# -- sampling (spec 19 B2) -----------------------------------------------------------------------


@pytest.mark.parametrize("dialect", LONG_TAIL)
def test_keyhash_without_a_hash_mapping_is_fl506(dialect: str) -> None:
    with pytest.raises(RenderError, match="FL506"):
        bridge.render_sqlglot(SAMPLED["keyhash_aggregate"], dialect)


def _unparen(node: Any) -> Any:
    while isinstance(node, sqlglot.exp.Paren):
        node = node.this
    return node


def _bucket(sql: str, dialect: str) -> Any:
    """The keyhash bucket expression: the left side of ``bucket < {{threshold}}``."""
    tree = sqlglot.parse_one(sql, read=dialect)
    (lt,) = [n for n in tree.find_all(sqlglot.exp.LT)]
    return _unparen(lt.this)


def _is_number(node: Any, value: int) -> bool:
    node = _unparen(node)
    return isinstance(node, sqlglot.exp.Literal) and node.this == str(value)


def test_keyhash_on_bridged_native_dialects_is_the_ruled_fold() -> None:
    b = 1_000_000
    # postgres: MOD(MOD(hashtextextended(s, 0), B) + B, B), signed fold, never ABS
    sql, _ = bridge.render_sqlglot(SAMPLED["keyhash_aggregate"], "postgres").bind("qmark")
    outer = _bucket(sql, "postgres")
    assert isinstance(outer, sqlglot.exp.Mod) and _is_number(outer.expression, b)
    add = _unparen(outer.this)
    assert isinstance(add, sqlglot.exp.Add) and _is_number(add.expression, b)
    inner = _unparen(add.this)
    assert isinstance(inner, sqlglot.exp.Mod) and _is_number(inner.expression, b)
    h = _unparen(inner.this)
    assert h.sql(dialect="postgres").upper().startswith("HASHTEXTEXTENDED(")
    assert _is_number(h.expressions[-1], 0)
    assert "ABS(" not in sql.upper()
    # the hash input is seed || '|' || CAST(key AS text)
    concat = _unparen(h.expressions[0])
    assert isinstance(concat, sqlglot.exp.DPipe)
    # duckdb: hash(s) % B, no fold (UBIGINT)
    sql, _ = bridge.render_sqlglot(SAMPLED["keyhash_aggregate"], "duckdb").bind("qmark")
    bucket = _bucket(sql, "duckdb")
    assert isinstance(bucket, sqlglot.exp.Mod) and _is_number(bucket.expression, b)
    assert _unparen(bucket.this).sql(dialect="duckdb").upper().startswith("HASH(")


def _sampled(method: str, seed: int = 7) -> ColumnAggregate:
    spec = SampleSpec(method, Decimal("0.05"), seed)  # type: ignore[arg-type]
    return ColumnAggregate(BIG, None, (Metric.ROW_COUNT,), sample=spec)


@pytest.mark.parametrize("dialect", [*LONG_TAIL, "dune", "exasol", "dremio"])
@pytest.mark.parametrize("method", ["system", "bernoulli"])
def test_a_table_sample_on_a_dialect_without_a_native_profile_is_fl506(
    dialect: str, method: str
) -> None:
    # 0.1.0 ruling: no engine verification exists for non-native dialects, so every
    # system/bernoulli sample there is FL506, whatever sqlglot could spell
    if not bridge.supports_dialect(dialect):
        pytest.skip(f"sqlglot {sqlglot.__version__} has no {dialect} dialect")
    assert get_profile(dialect) is None
    with pytest.raises(RenderError, match="FL506"):
        bridge.render_sqlglot(_sampled(method), dialect)
    with pytest.raises(RenderError, match="FL506"):
        bridge.to_sqlglot(A.TableSample(method, Decimal("0.05"), 7), dialect)


NATIVE_STYLES = sorted(
    (name, method)
    for name in native_dialects()
    for method in ("system", "bernoulli")
    if name in SQLGLOT_NAMES
)


@pytest.mark.parametrize(("name", "method"), NATIVE_STYLES)
def test_a_native_dialect_through_the_bridge_uses_its_profile_sample_style(
    name: str, method: str
) -> None:
    profile = get_profile(name)
    assert profile is not None
    plan = _sampled(method)
    if not profile.supports_sample(method):  # a None style is FL506, as natively
        with pytest.raises(RenderError, match="FL506"):
            bridge.render_sqlglot(plan, name)
        return
    style = profile.sample_styles[method]
    assert style is not None
    want = style.format(percent="5", fraction="0.05", seed=7)
    probe = renderer_for(name, prefer="sqlglot")(plan)
    assert probe.tier == "best_effort"
    assert want in probe.sql_template  # the profile's declared spelling, verbatim
    assert want in render(plan, profile).sql_template


def test_bridged_native_samples_keep_the_profiles_seed_range() -> None:
    with pytest.raises(RenderError, match="FL506"):
        bridge.render_sqlglot(_sampled("bernoulli", seed=-1), "duckdb")
    assert "TABLESAMPLE SYSTEM (5) REPEATABLE (7)" in (
        bridge.render_sqlglot(_sampled("system"), "postgres").sql_template
    )


@pytest.mark.parametrize("dialect", ["duckdb", "postgres"])
@pytest.mark.parametrize("method", ["system", "bernoulli"])
def test_block_and_row_samples_on_multi_read_plans_are_fl405(dialect: str, method: str) -> None:
    spec = SampleSpec(method, Decimal("0.05"), 7)  # type: ignore[arg-type]
    plans = [
        ValueMembership(ORDERS, Ident("status"), ("paid",), sample=spec),
        KeyOverlap((c(ITEMS, "order_id"),), (c(ORDERS, "id"),), sample=spec),
        ColumnAggregate(ORDERS, ID, (Metric.MAX_PER_KEY,), sample=spec),
        CompositeKeyAggregate(ITEMS, (Ident("order_id"),), (Metric.DUPLICATE_KEYS,), sample=spec),
    ]
    for plan in plans:
        with pytest.raises(RenderError, match="FL405"):
            renderer_for(dialect, prefer="sqlglot")(plan)  # type: ignore[arg-type]
        with pytest.raises(RenderError, match="FL405"):
            bridge.render_sqlglot(plan, "presto")


def test_fraction_one_renders_no_sample() -> None:
    plan = ColumnAggregate(BIG, None, (Metric.ROW_COUNT,), sample=SampleSpec("system", Decimal(1)))
    sql = bridge.render_sqlglot(plan, "teradata").sql_template
    assert "SAMPLE" not in sql.replace("sampled_rows", "")


# -- to_sqlglot ----------------------------------------------------------------------------------


def test_to_sqlglot_converts_expressions_and_statements() -> None:
    e = bridge.to_sqlglot(A.Compare("=", A.Column(Ident("id")), A.Literal(1)))
    assert isinstance(e, sqlglot.exp.Expression)
    assert e.sql() == "id = 1"
    tree = bridge.to_sqlglot(A.Select((A.Aliased(A.Agg("COUNT", None), "n"),), A.TableName(ORDERS)))
    assert tree.sql(dialect="presto") == "SELECT COUNT(*) AS n FROM orders"
    with pytest.raises(RenderError, match="FL506"):  # keyhash needs a target dialect
        bridge.to_sqlglot(
            A.KeyHash((A.Cast(A.Column(ID), "string"),), A.ParamRef(Param("s", "0", "string")))
        )
    with pytest.raises(RenderError, match="FL500"):
        bridge.to_sqlglot(object())


# -- fragments (spec 10, R6) ---------------------------------------------------------------------


@pytest.mark.parametrize("dialect", LONG_TAIL)
def test_fragments_render_through_the_bridge(dialect: str) -> None:
    frag = render_filter(LIVE, table=ORDERS, dialect=dialect, alias="o")
    assert frag.tier == "best_effort" and frag.dialect == dialect
    sql, values = frag.bind("qmark")
    sqlglot.parse_one(f"SELECT 1 FROM orders o WHERE {sql}", read=dialect)
    paid = FCompare(Ident("status"), "=", Param("p1", "paid", "string"))
    join = render_join(
        [EdgeRef(c(ITEMS, "order_id"), c(ORDERS, "id"))], dialect, filters={ORDERS: paid}
    )
    assert join.tier == "best_effort" and join.sql_template.startswith("FROM ")
    sql, values = join.bind("qmark")
    assert values == ("paid",)
    sqlglot.parse_one("SELECT COUNT(*) " + sql, read=dialect)


def test_fragment_guards_through_the_bridge() -> None:
    with pytest.raises(RenderError, match="FL502"):
        render_filter(LIVE, table=TableRef(None, None, Ident("a;b", quoted=True)), dialect="presto")
    with pytest.raises(RenderError, match="FL504"):
        render_filter(LIVE, dialect="no_such_dialect")


def test_bridged_fragments_execute_like_native_ones_on_duckdb(con: Any) -> None:
    paid = FCompare(Ident("status"), "=", Param("p1", "paid", "string"))
    one_item = FCompare(Ident("n"), "=", Param("p1", 1, "integer"))
    edges = [EdgeRef(c(ITEMS, "order_id"), c(ORDERS, "id"))]
    filters = {ORDERS: paid, ITEMS: one_item}
    results = []
    for prefer in ("native", "sqlglot"):
        frag = render_join(edges, "duckdb", filters=filters, prefer=prefer)  # type: ignore[arg-type]
        sql, values = frag.bind("qmark")
        results.append(con.execute("SELECT COUNT(*) " + sql, list(values)).fetchone())
        where = render_filter(LIVE, table=ORDERS, dialect="duckdb", alias="o", prefer=prefer)  # type: ignore[arg-type]
        sql, values = where.bind("qmark")
        results.append(
            con.execute("SELECT COUNT(*) FROM orders o WHERE " + sql, list(values)).fetchone()
        )
    assert results == [(1,), (5,), (1,), (5,)]
    bridged = render_filter(LIVE, dialect="duckdb", prefer="sqlglot")
    assert bridged.tier == "best_effort"


# -- native dialects through the bridge, custom profile objects, long-tail metadata ------------


@pytest.mark.parametrize("name", sorted(EXACT))
def test_trino_native_through_the_bridge_renders_and_parses_back(name: str) -> None:
    assert get_profile("trino") is not None, "trino is native (Amendment A, R7)"
    probe = renderer_for("trino", prefer="sqlglot")(EXACT[name])  # type: ignore[arg-type]
    assert probe.tier == "best_effort" and probe.dialect == "trino"
    sql, values = probe.bind("qmark")
    assert sql.count("?") == len(values)  # type: ignore[arg-type]
    assert isinstance(sqlglot.parse_one(sql, read="trino"), sqlglot.exp.Query)


def test_a_profile_object_is_threaded_through_not_swapped() -> None:
    # an unregistered profile under a registered name: the bridge must use THIS object
    custom = dataclasses.replace(DUCK, max_table_parts=1)
    short = dataclasses.replace(DUCK, max_identifier_length=10)
    two_parts = TableRef(None, Ident("main"), Ident("orders"))
    assert render_filter(LIVE, table=two_parts, dialect="duckdb", prefer="sqlglot")
    with pytest.raises(RenderError, match="FL505"):
        render_filter(LIVE, table=two_parts, dialect=custom, prefer="sqlglot")
    with pytest.raises(RenderError, match="FL502"):  # 11 bytes > 10
        render_filter(FIsNull(Ident("longname123")), dialect=short, prefer="sqlglot")
    assert render_filter(FIsNull(Ident("longname12")), dialect=short, prefer="sqlglot")
    edges = [EdgeRef(c(ITEMS, "order_id"), c(ORDERS, "id"))]
    with pytest.raises(RenderError, match="FL502"):  # 'order_items' is 11 bytes
        render_join(edges, short, prefer="sqlglot")
    frag = render_filter(LIVE, table=ORDERS, dialect=custom, prefer="sqlglot")
    assert frag.tier == "best_effort" and frag.dialect == "duckdb"
    with pytest.raises(RenderError, match="FL505"):
        bridge.render_sqlglot(ColumnAggregate(two_parts, None, (Metric.ROW_COUNT,)), custom)
    probe = bridge.render_sqlglot(ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,)), custom)
    assert probe.dialect == "duckdb" and probe.tier == "best_effort"


def test_long_tail_metadata_compares_quoted_names_exactly_and_unquoted_case_folded() -> None:
    quoted = TableRef(None, Ident("Sales", quoted=True), Ident("Orders", quoted=True))
    probe = bridge.render_sqlglot(MetadataQuery(quoted, Ident("ID")), "presto")
    sql = probe.sql_template
    assert "table_name = {{cat1}}" in sql and "table_schema = {{cat2}}" in sql
    assert "LOWER(column_name) = LOWER({{cat3}})" in sql
    assert [p.value for p in probe.params] == ["Orders", "Sales", "ID"]
    unquoted = bridge.render_sqlglot(MetadataQuery(ORDERS), "presto").sql_template
    assert "LOWER(table_name) = LOWER({{cat1}})" in unquoted
