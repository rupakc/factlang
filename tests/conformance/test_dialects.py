"""Dialect conformance: every native profile renders every primitive safely (spec 8, 18, 19).

Parametrised over ``native_dialects()``, so a profile registered later (Task 15) joins every
test here automatically.
"""

from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

import pytest

from factlang.ir import (
    KEY_METRICS,
    TABLE_METRICS,
    ColumnAggregate,
    CompositeKeyAggregate,
    KeyOverlap,
    MetadataQuery,
    Metric,
    PatternCoverage,
    SampleSpec,
    ValueMembership,
)
from factlang.semantics.filters import FAnd, FBool, FCompare, FIn, FIsNull, FNot, Param
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef
from factlang.sql import (
    SQLGLOT_NAMES,
    RenderError,
    family_of,
    get_profile,
    native_dialects,
    render,
    render_filter,
    render_join,
)
from factlang.sql.profile import DialectProfile

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "type_names"
PROFILES = native_dialects()

ORDERS = TableRef(None, None, Ident("orders"))
CUSTOMERS = TableRef(None, Ident("shop"), Ident("customers"))
ITEMS = TableRef(Ident("warehouse"), Ident("shop"), Ident("order_items"))
ID, STATUS, AMOUNT = Ident("id"), Ident("status"), Ident("amount")
HOSTILE = "x'; DROP TABLE orders; --"


def cref(t: TableRef, name: str) -> ColumnRef:
    return ColumnRef(t, Ident(name))


PAID = FCompare(STATUS, "=", Param("p1", "paid", "string"))
LIVE = FAnd((FNot(FBool(Ident("deleted"))), FIn(STATUS, (Param("p1", "a", "string"),))))


def _column_aggregates() -> list[object]:
    plans: list[object] = []
    for m in Metric:
        column = None if m in TABLE_METRICS else AMOUNT
        plans.append(ColumnAggregate(ORDERS, column, (m,)))
    plans.append(ColumnAggregate(ORDERS, AMOUNT, tuple(Metric), filter=PAID))
    return plans


EXACT_PLANS: list[object] = [
    *_column_aggregates(),
    *(CompositeKeyAggregate(ITEMS, (ID, STATUS), (m,)) for m in sorted(KEY_METRICS, key=str)),
    CompositeKeyAggregate(ITEMS, (ID, STATUS), tuple(sorted(KEY_METRICS, key=str)), filter=LIVE),
    ValueMembership(ORDERS, STATUS, ("paid", "open")),
    ValueMembership(ORDERS, STATUS, (), report_limit=5, filter=PAID),
    ValueMembership(ORDERS, STATUS, ("x",), split=":"),
    KeyOverlap((cref(ITEMS, "order_id"),), (cref(ORDERS, "id"),)),
    KeyOverlap(
        (cref(ITEMS, "order_id"), cref(ITEMS, "shop_id")),
        (cref(CUSTOMERS, "id"), cref(CUSTOMERS, "shop_id")),
        cast_to="string",
        measure_multiplicity=True,
        filter=PAID,
        right_filter=FIsNull(Ident("deleted_at")),
    ),
    PatternCoverage(ORDERS, STATUS, "%Paid%", filter=PAID),
    MetadataQuery(ORDERS),
    MetadataQuery(CUSTOMERS, STATUS),
    MetadataQuery(ITEMS),
]

KEYHASH = SampleSpec("keyhash", Decimal("0.01"), seed=42, key=(ID,))
SAMPLES = {
    "keyhash": KEYHASH,
    "system": SampleSpec("system", Decimal("0.05"), seed=7),
    "bernoulli": SampleSpec("bernoulli", Decimal("0.25"), seed=3),
}
KEYHASH_PLANS: list[object] = [
    ColumnAggregate(ORDERS, ID, (Metric.ROW_COUNT, Metric.MAX_PER_KEY), sample=KEYHASH),
    CompositeKeyAggregate(ITEMS, (ID, STATUS), (Metric.DISTINCT_KEYS,), sample=KEYHASH),
    ValueMembership(ORDERS, STATUS, ("paid",), sample=KEYHASH),
    KeyOverlap((cref(ITEMS, "id"),), (cref(ORDERS, "id"),), sample=KEYHASH, right_sample=KEYHASH),
    PatternCoverage(ORDERS, STATUS, "%a%", sample=KEYHASH),
]


def profile(name: str) -> DialectProfile:
    p = get_profile(name)
    assert p is not None
    return p


def plan_id(plan: object) -> str:
    return type(plan).__name__


def words(sql: str) -> list[str]:
    return [w.upper() for w in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", sql)]


def hash_functions(p: DialectProfile) -> set[str]:
    """Function names in the profile's keyhash expression that compute a hash."""
    assert p.keyhash_expr is not None
    names = re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(", p.keyhash_expr)
    found = {n.upper() for n in names if re.search("hash|fingerprint|md5", n, re.I)}
    assert found, "a keyhash expression must call a hash function"
    return found


def test_there_are_native_profiles() -> None:
    assert {"duckdb", "postgres"} <= set(PROFILES)
    assert sorted(PROFILES) == PROFILES


@pytest.mark.parametrize("name", PROFILES)
@pytest.mark.parametrize("plan", EXACT_PLANS + KEYHASH_PLANS, ids=plan_id)
def test_every_primitive_and_metric_renders(name: str, plan: object) -> None:
    probe = render(plan, profile(name))  # type: ignore[arg-type]
    assert probe.dialect == name and probe.tier == profile(name).tier
    assert probe.arity is plan.arity  # type: ignore[attr-defined]
    sql = probe.sql_template
    assert sql.startswith(("SELECT ", "WITH ")) and ";" not in sql
    for alias in plan.result_columns():  # type: ignore[attr-defined]
        assert re.search(rf"\bAS {alias}\b", sql), alias


@pytest.mark.parametrize("name", PROFILES)
@pytest.mark.parametrize("plan", EXACT_PLANS, ids=plan_id)
def test_forbidden_tokens_in_exact_plans(name: str, plan: object) -> None:
    p = profile(name)
    toks = words(render(plan, p).sql_template)  # type: ignore[arg-type]
    assert "TABLESAMPLE" not in toks and "SAMPLE" not in toks
    if p.keyhash_expr is not None:
        assert not hash_functions(p) & set(toks)
    limits = [t for t in toks if t in ("LIMIT", "TOP", "FETCH")]
    if isinstance(plan, ValueMembership):
        assert len(limits) == 1  # only the ranked list of undeclared values
    else:
        assert limits == []


TABLESAMPLE_PLANS: list[object] = [
    ColumnAggregate(ORDERS, AMOUNT, (Metric.ROW_COUNT, Metric.SUM), sample=SAMPLES[m])
    for m in ("system", "bernoulli")
] + [PatternCoverage(ORDERS, STATUS, "%a%", sample=SAMPLES["bernoulli"])]


@pytest.mark.parametrize("name", PROFILES)
@pytest.mark.parametrize("plan", KEYHASH_PLANS + TABLESAMPLE_PLANS, ids=plan_id)
def test_no_limit_in_sampled_plans_outside_value_membership(name: str, plan: object) -> None:
    p = profile(name)
    try:
        toks = words(render(plan, p).sql_template)  # type: ignore[arg-type]
    except RenderError as err:
        assert "FL506" in str(err)  # a method the profile does not declare
        return
    limits = [t for t in toks if t in ("LIMIT", "TOP", "FETCH")]
    assert len(limits) == (1 if isinstance(plan, ValueMembership) else 0)


@pytest.mark.parametrize("name", PROFILES)
@pytest.mark.parametrize("method", sorted(SAMPLES))
def test_each_sample_method_renders_or_is_fl506(name: str, method: str) -> None:
    p = profile(name)
    spec = SAMPLES[method]
    key = (ID,) if method == "keyhash" else ()
    plan = ColumnAggregate(ORDERS, ID, (Metric.ROW_COUNT,), sample=spec)
    assert spec.key == key
    supported = (
        p.keyhash_expr is not None if method == "keyhash" else bool(p.sample_styles.get(method))
    )
    if not supported:
        with pytest.raises(RenderError, match="FL506"):
            render(plan, p)
        return
    sql = render(plan, p).sql_template
    toks = set(words(sql))
    if method == "keyhash":
        assert hash_functions(p) <= toks
        assert "TABLESAMPLE" not in toks and "SAMPLE" not in toks
    else:
        assert {"TABLESAMPLE", "SAMPLE"} & toks or "USING" in toks
        assert method.upper() in toks


@pytest.mark.parametrize("name", PROFILES)
def test_fraction_one_renders_no_sample_text(name: str) -> None:
    p = profile(name)
    for method in SAMPLES:
        key = (ID,) if method == "keyhash" else ()
        spec = SampleSpec(method, Decimal(1), seed=1, key=key)  # type: ignore[arg-type]
        sql = render(ColumnAggregate(ORDERS, ID, (Metric.ROW_COUNT,), sample=spec), p).sql_template
        toks = set(words(sql))
        assert not {"TABLESAMPLE", "SAMPLE"} & toks
        if p.keyhash_expr is not None:
            assert not hash_functions(p) & toks


@pytest.mark.parametrize("name", PROFILES)
@pytest.mark.parametrize("plan", KEYHASH_PLANS, ids=plan_id)
def test_keyhash_on_every_sampled_primitive(name: str, plan: object) -> None:
    p = profile(name)
    if p.keyhash_expr is None:
        with pytest.raises(RenderError, match="FL506"):
            render(plan, p)  # type: ignore[arg-type]
        return
    probe = render(plan, p)  # type: ignore[arg-type]
    assert hash_functions(p) <= set(words(probe.sql_template))
    seeds = [x for x in probe.params if x.value == "42"]
    assert seeds and all(x.family == "string" for x in seeds)
    assert any(x.value == 10_000 and x.family == "integer" for x in probe.params)


@pytest.mark.parametrize("name", PROFILES)
def test_identifier_rules(name: str) -> None:
    p = profile(name)
    q = p.quote_char
    close = "]" if q == "[" else q

    def sql_for(ident: Ident) -> str:
        t = TableRef(None, None, Ident("orders"))
        return render(ColumnAggregate(t, ident, (Metric.NON_NULL,)), p).sql_template

    # unquoted stays unquoted, case untouched (the warehouse applies its own rules)
    assert "COUNT(MixedCase)" in sql_for(Ident("MixedCase"))
    # a reserved word is quoted after the profile's unquoted-case fold
    folded = {"upper": "SELECT", "lower": "select", "preserve": "Select"}[p.unquoted_case]
    assert f"COUNT({q}{folded}{close})" in sql_for(Ident("Select"))
    # quoted keeps its case; an embedded quote character is doubled
    assert f"COUNT({q}Order Items{close})" in sql_for(Ident("Order Items", quoted=True))
    assert f"COUNT({q}a{close}{close}b{close})" in sql_for(Ident(f"a{close}b", quoted=True))
    # refused, never sanitised
    for bad in ("a\x00b", "a\nb", "a\rb", "{{p1}}", "x}}y", "a" * (p.max_identifier_length + 1)):
        with pytest.raises(RenderError, match="FL502"):
            sql_for(Ident(bad, quoted=True))
    for bad in ("a b", "x) OR (1=1", "1abc", ""):
        with pytest.raises(RenderError, match="FL502"):
            sql_for(Ident(bad))
    # the longest allowed name renders
    sql_for(Ident("a" * p.max_identifier_length))


@pytest.mark.parametrize("name", PROFILES)
def test_too_many_table_parts_is_fl505(name: str) -> None:
    p = profile(name)
    if p.max_table_parts >= 3:
        pytest.skip("a TableRef holds at most three parts")
    with pytest.raises(RenderError, match="FL505"):
        render(ColumnAggregate(ITEMS, None, (Metric.ROW_COUNT,)), p)


@pytest.mark.parametrize("name", PROFILES)
def test_hostile_literal_only_in_params(name: str) -> None:
    p = profile(name)
    hostile = FCompare(STATUS, "=", Param("p1", HOSTILE, "string"))
    plans = [
        ColumnAggregate(ORDERS, ID, (Metric.ROW_COUNT,), filter=hostile),
        ValueMembership(ORDERS, STATUS, (HOSTILE,), split=";", filter=hostile),
        PatternCoverage(ORDERS, STATUS, HOSTILE),
        KeyOverlap((cref(ITEMS, "id"),), (cref(ORDERS, "id"),), right_filter=hostile),
    ]
    for plan in plans:
        probe = render(plan, p)
        assert "DROP" not in probe.sql_template.upper() and ";" not in probe.sql_template
        assert any(HOSTILE.lower() in str(x.value).lower() for x in probe.params)
    frag = render_filter(hostile, table=ORDERS, dialect=name)
    assert "DROP" not in frag.sql_template and [x.value for x in frag.params] == [HOSTILE]
    join = render_join(
        [EdgeRef(cref(ITEMS, "order_id"), cref(ORDERS, "id"))],
        name,
        filters={ORDERS: hostile, ITEMS: hostile},
    )
    assert "DROP" not in join.sql_template
    assert [x.value for x in join.params] == [HOSTILE, HOSTILE]


@pytest.mark.parametrize("name", PROFILES)
def test_render_filter_and_join_render(name: str) -> None:
    frag = render_filter(LIVE, table=ORDERS, dialect=name, alias="o")
    assert frag.dialect == name and frag.tier == profile(name).tier
    assert "o.deleted" in frag.sql_template and "o.status" in frag.sql_template
    assert ";" not in frag.sql_template
    join = render_join(
        [
            EdgeRef(cref(ITEMS, "order_id"), cref(ORDERS, "id")),
            EdgeRef(cref(ORDERS, "customer_id"), cref(CUSTOMERS, "id")),
        ],
        dialect=name,
        filters={ITEMS: PAID, CUSTOMERS: FIsNull(Ident("deleted_at"))},
        aliases={ORDERS: "o"},
    )
    sql = join.sql_template
    assert sql.startswith("FROM ") and sql.count(" JOIN ") == 2
    assert " WHERE t1.status = " in sql or " WHERE (t1.status = " in sql
    assert "o.customer_id" in sql


def _parse_ok(sql: str, dialect: str) -> None:
    sqlglot = pytest.importorskip("sqlglot")
    tree = sqlglot.parse_one(sql, read=dialect)
    assert tree is not None


@pytest.mark.parametrize("name", PROFILES)
@pytest.mark.parametrize("plan", EXACT_PLANS + KEYHASH_PLANS, ids=plan_id)
def test_sqlglot_parses_rendered_sql(name: str, plan: object) -> None:
    sql, _ = render(plan, profile(name)).bind("qmark")  # type: ignore[arg-type]
    _parse_ok(sql, SQLGLOT_NAMES[name])


@pytest.mark.parametrize("name", PROFILES)
def test_sqlglot_parses_table_samples(name: str) -> None:
    p = profile(name)
    for method in ("system", "bernoulli"):
        if not p.sample_styles.get(method):
            continue
        plan = ColumnAggregate(ORDERS, ID, (Metric.ROW_COUNT,), sample=SAMPLES[method])
        sql, _ = render(plan, p).bind("qmark")
        _parse_ok(sql, SQLGLOT_NAMES[name])


@pytest.mark.parametrize("name", PROFILES)
def test_type_families(name: str) -> None:
    fixture = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    assert len(fixture) >= 15
    p = profile(name)
    got = {native: family_of(native, p) for native in fixture}
    assert got == fixture


def test_sqlglot_names_cover_every_dialect_in_the_spec() -> None:
    expected = {
        "bigquery": "bigquery",
        "snowflake": "snowflake",
        "redshift": "redshift",
        "postgres": "postgres",
        "duckdb": "duckdb",
        "databricks": "databricks",
        "clickhouse": "clickhouse",
        "tsql": "tsql",
        "mysql": "mysql",
        "oracle": "oracle",
        "trino": "trino",
    }
    assert dict(SQLGLOT_NAMES) == expected
    assert set(PROFILES) <= set(SQLGLOT_NAMES)
