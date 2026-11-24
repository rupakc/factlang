"""Native printer, profiles and the dialect registry (spec 8.3, 8.4, 18 R6, 19 B2)."""

from __future__ import annotations

import dataclasses
from decimal import Decimal

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
from factlang.semantics.filters import FCompare, Param
from factlang.semantics.refs import ColumnRef, Ident, TableRef
from factlang.sql import (
    SQLGLOT_NAMES,
    DialectProfile,
    RenderError,
    family_of,
    get_profile,
    lower,
    native_dialects,
    print_select,
    register_profile,
    registry,
    render,
    render_filter,
    render_join,
    renderer_for,
)
from factlang.sql import ast as A
from factlang.sql.printer import Printer
from factlang.sql.profiles import BUILTIN_PROFILES

ORDERS = TableRef(None, None, Ident("orders"))
SHOP_ORDERS = TableRef(Ident("wh"), Ident("Shop"), Ident("Orders", quoted=True))
ID, STATUS = Ident("id"), Ident("status")


def prof(name: str) -> DialectProfile:
    p = get_profile(name)
    assert p is not None
    return p


DUCK, PG = prof("duckdb"), prof("postgres")


def with_(p: DialectProfile, **changes: object) -> DialectProfile:
    return dataclasses.replace(p, **changes)  # type: ignore[arg-type]


def count_if_sql(p: DialectProfile) -> str:
    plan = CompositeKeyAggregate(ORDERS, (ID, STATUS), (Metric.NON_NULL,))
    return render(plan, p).sql_template


# -- registry -----------------------------------------------------------------------------


def test_builtin_profiles_are_registered() -> None:
    assert {p.name for p in BUILTIN_PROFILES} >= {"duckdb", "postgres"}  # + Task 15's nine
    assert {"duckdb", "postgres"} <= set(native_dialects())
    assert get_profile("duckdb") is DUCK and get_profile("nope") is None
    assert DUCK.tier == PG.tier == "verified"


def test_register_profile_refuses_duplicates_unless_replace() -> None:
    custom = with_(DUCK, name="custom_test")
    register_profile(custom)
    try:
        with pytest.raises(ValueError, match="already registered"):
            register_profile(custom)
        other = with_(custom, max_identifier_length=10)
        register_profile(other, replace=True)
        assert get_profile("custom_test") is other
        assert "custom_test" in native_dialects()
    finally:
        registry._PROFILES.pop("custom_test", None)
    with pytest.raises(TypeError):
        register_profile("duckdb")  # type: ignore[arg-type]


def test_renderer_for_native_and_unknown() -> None:
    plan = ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,))
    probe = renderer_for("postgres")(plan)
    assert probe.dialect == "postgres"
    with pytest.raises(RenderError, match="FL504") as err:
        renderer_for("no_such_dialect")
    msg = str(err.value)
    assert "duckdb" in msg and "postgres" in msg and "pip install factlang[sqlglot]" in msg
    # forcing the bridge when it is absent says so, not "unknown dialect"
    with pytest.raises(RenderError, match="sqlglot bridge not installed") as forced:
        renderer_for("duckdb", prefer="sqlglot")
    assert "unknown dialect" not in str(forced.value)
    assert "pip install factlang[sqlglot]" in str(forced.value)
    with pytest.raises(ValueError, match="prefer"):
        renderer_for("duckdb", prefer="other")  # type: ignore[arg-type]


def test_renderer_for_uses_the_bridge_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class Bridge:
        @staticmethod
        def render(plan: object, dialect: str) -> str:
            calls.append(dialect)
            return "bridged"

    monkeypatch.setattr(registry, "_bridge", lambda: Bridge)
    assert renderer_for("teradata")(ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,))) == "bridged"
    renderer_for("duckdb", prefer="sqlglot")(ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,)))
    assert calls == ["teradata", "duckdb"]


def test_bridge_import_errors_other_than_a_missing_module_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = registry.importlib.import_module

    def fake(name: str) -> object:
        if name == "factlang.sql.sqlglot_bridge":
            raise ModuleNotFoundError("No module named 'factlang.sql.sqlglot_bridge'", name=name)
        return real(name)

    monkeypatch.setattr(registry.importlib, "import_module", fake)
    assert registry._bridge() is None

    def broken(name: str) -> object:
        if name == "factlang.sql.sqlglot_bridge":
            raise ModuleNotFoundError("No module named 'yaml'", name="yaml")
        return real(name)

    monkeypatch.setattr(registry.importlib, "import_module", broken)
    with pytest.raises(ModuleNotFoundError, match="yaml"):
        registry._bridge()

    def bad(name: str) -> object:
        if name == "factlang.sql.sqlglot_bridge":
            raise ImportError("cannot import name 'x'")
        return real(name)

    monkeypatch.setattr(registry.importlib, "import_module", bad)
    with pytest.raises(ImportError, match="cannot import name"):
        registry._bridge()


def test_sqlglot_names() -> None:
    assert SQLGLOT_NAMES["trino"] == "trino" and SQLGLOT_NAMES["tsql"] == "tsql"


# -- profile validation -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"count_if_style": "nope"}, "count_if_style"),
        ({"limit_style": "nope"}, "limit_style"),
        ({"unquoted_case": "title"}, "unquoted_case"),
        ({"tier": "gold"}, "tier"),
        ({"quote_char": "'"}, "quote_char"),
        ({"max_table_parts": 0}, "max_table_parts"),
        ({"max_identifier_length": 0}, "max_identifier_length"),
        ({"cast_types": {"wat": "X"}}, "cast_types"),
        ({"param_types": {"wat": "X"}}, "param_types"),
        ({"type_families": (("int", "wat"),)}, "type_families"),
        ({"type_families": (("(", "integer"),)}, "type_families"),
        ({"sample_styles": {"keyhash": "x"}}, "sample_styles"),
        ({"sample_styles": {"system": "TABLESAMPLE ({percent}); DROP"}}, ";"),
        ({"keyhash_expr": "hash(x)"}, "keyhash_expr"),
        ({"information_schema_sql": "SELECT 1"}, "information_schema_sql"),
        ({"now_sql": "NOW();"}, ";"),
        ({"sample_seed_range": (5, 1)}, "sample_seed_range"),
        ({"quoted_catalog_match": "fuzzy"}, "quoted_catalog_match"),
        ({"unquoted_catalog_match": "preserve"}, "unquoted_catalog_match"),
        ({"cast_params": True, "param_types": {"string": "TEXT"}}, "every parameter family"),
    ],
)
def test_profile_validation(change: dict[str, object], match: str) -> None:
    with pytest.raises((ValueError, TypeError), match=match):
        with_(DUCK, **change)


def test_profile_folds_reserved_words_and_freezes_mappings() -> None:
    p = with_(DUCK, reserved_words=frozenset({"order"}), cast_types={"string": "TEXT"})
    assert "ORDER" in p.reserved_words
    with pytest.raises(TypeError):
        p.cast_types["integer"] = "X"  # type: ignore[index]
    assert p.supports_sample("bernoulli") and p.supports_sample("keyhash")
    assert not p.supports_sample("system")  # DuckDB SYSTEM is not repeatable: FL506
    assert not with_(p, keyhash_expr=None).supports_sample("keyhash")


# -- printing -----------------------------------------------------------------------------


def test_count_if_styles_are_zero_on_no_rows() -> None:
    assert "COALESCE(COUNT_IF(" in count_if_sql(DUCK)
    assert "COUNT(*) FILTER (WHERE " in count_if_sql(PG)
    assert "COALESCE(COUNTIF(" in count_if_sql(with_(DUCK, count_if_style="countif"))
    sql = count_if_sql(with_(DUCK, count_if_style="sum_case"))
    assert "COALESCE(SUM(CASE WHEN " in sql and " THEN 1 ELSE 0 END), 0)" in sql


def test_limit_styles_and_union_branch_parenthesised() -> None:
    plan = ValueMembership(ORDERS, STATUS, ("a",), report_limit=7)
    sql = render(plan, DUCK).sql_template
    assert " UNION ALL (SELECT " in sql and sql.count("LIMIT 7)") == 1
    fetch = render(plan, with_(DUCK, limit_style="fetch")).sql_template
    assert "FETCH FIRST 7 ROWS ONLY)" in fetch and "LIMIT" not in fetch
    top = render(plan, with_(DUCK, limit_style="top")).sql_template
    assert "UNION ALL SELECT * FROM (SELECT TOP 7 " in top and "LIMIT" not in top


def test_top_level_union_is_wrapped_so_the_statement_starts_with_select() -> None:
    cols = (A.Aliased(A.Literal(1), "x"),)
    union = A.UnionAll(
        (A.Select(cols, A.TableName(ORDERS)), A.Select(cols, A.TableName(ORDERS), limit=1))
    )
    sql, params = print_select(union, DUCK)
    assert sql.startswith("SELECT * FROM (") and params == ()


def test_casts_and_fl503() -> None:
    plan = KeyOverlap((ColumnRef(ORDERS, ID),), (ColumnRef(SHOP_ORDERS, ID),), cast_to="string")
    assert "CAST(id AS VARCHAR)" in render(plan, DUCK).sql_template
    assert "CAST(id AS TEXT)" in render(plan, PG).sql_template
    bad = KeyOverlap((ColumnRef(ORDERS, ID),), (ColumnRef(SHOP_ORDERS, ID),), cast_to="struct")
    with pytest.raises(RenderError, match="FL503"):
        render(bad, DUCK)


def test_postgres_casts_every_param_to_its_type() -> None:
    f = FCompare(STATUS, "=", Param("p1", "paid", "string"))
    probe = render(ColumnAggregate(ORDERS, ID, (Metric.ROW_COUNT,), filter=f), PG)
    assert "status = CAST({{p1}} AS TEXT)" in probe.sql_template
    assert (
        "status = {{p1}}"
        in render(ColumnAggregate(ORDERS, ID, (Metric.ROW_COUNT,), filter=f), DUCK).sql_template
    )


def test_qualified_table_names_and_quoting() -> None:
    sql = render(ColumnAggregate(SHOP_ORDERS, None, (Metric.ROW_COUNT,)), PG).sql_template
    assert 'FROM wh.Shop."Orders"' in sql
    reserved = TableRef(None, Ident("Order"), Ident("Select"))
    assert (
        'FROM "order"."select"'
        in render(ColumnAggregate(reserved, None, (Metric.ROW_COUNT,)), PG).sql_template
    )
    assert (
        'FROM "Order"."Select"'
        in render(ColumnAggregate(reserved, None, (Metric.ROW_COUNT,)), DUCK).sql_template
    )


def test_fl505_when_a_profile_accepts_fewer_parts() -> None:
    two = with_(DUCK, max_table_parts=2)
    with pytest.raises(RenderError, match="FL505"):
        render(ColumnAggregate(SHOP_ORDERS, None, (Metric.ROW_COUNT,)), two)
    render(ColumnAggregate(TableRef(None, ID, ID), None, (Metric.ROW_COUNT,)), two)
    with pytest.raises(RenderError, match="FL505"):
        render_filter(
            FCompare(STATUS, "=", Param("p1", "x", "string")), table=SHOP_ORDERS, dialect=two
        )


def test_identifier_length_counts_utf8_bytes() -> None:
    name = "é" * 32  # 64 bytes, 32 characters: Postgres (63 bytes) would truncate it
    with pytest.raises(RenderError, match="FL502"):
        render(ColumnAggregate(ORDERS, Ident(name, quoted=True), (Metric.NON_NULL,)), PG)
    render(ColumnAggregate(ORDERS, Ident(name, quoted=True), (Metric.NON_NULL,)), DUCK)


def test_semicolon_in_an_identifier_is_refused() -> None:
    with pytest.raises(RenderError, match="FL502"):
        render(ColumnAggregate(ORDERS, Ident("a;b", quoted=True), (Metric.NON_NULL,)), DUCK)


def test_guard_refuses_a_statement_that_is_not_a_select() -> None:
    evil = with_(DUCK, overrides={A.Now: lambda pr, node: "1; DROP TABLE orders"})
    with pytest.raises(RenderError, match="guard"):
        render(ColumnAggregate(ORDERS, None, (Metric.NOW,)), evil)


def test_overrides_print_a_node_differently() -> None:
    p = with_(DUCK, overrides={A.Now: lambda pr, node: "now_override()"})
    sql = render(ColumnAggregate(ORDERS, None, (Metric.NOW,)), p).sql_template
    assert "now_override() AS server_now" in sql


def test_brackets_quote_char() -> None:
    p = with_(DUCK, quote_char="[")
    sql = render(ColumnAggregate(ORDERS, Ident("a]b", quoted=True), (Metric.NON_NULL,)), p)
    assert "COUNT([a]]b])" in sql.sql_template


# -- sampling -----------------------------------------------------------------------------


def test_keyhash_matches_the_spec_table() -> None:
    spec = SampleSpec("keyhash", Decimal("0.25"), seed=9, key=(ID, STATUS))
    plan = ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,), sample=spec)
    duck = render(plan, DUCK)
    s = "({{p1}} || '|' || CAST(id AS VARCHAR) || '|' || CAST(status AS VARCHAR))"
    assert f"(hash({s}) % 1000000) < {{{{p2}}}}" in duck.sql_template
    assert [(p.value, p.family) for p in duck.params] == [("9", "string"), (250000, "integer")]
    pg = render(plan, PG).sql_template
    s_pg = "(CAST({{p1}} AS TEXT) || '|' || CAST(id AS TEXT) || '|' || CAST(status AS TEXT))"
    want = f"MOD(MOD(hashtextextended({s_pg}, 0), 1000000) + 1000000, 1000000)"
    assert f"{want} < CAST({{{{p2}}}} AS BIGINT)" in pg


def test_table_sample_spellings() -> None:
    plan = ColumnAggregate(
        ORDERS, None, (Metric.ROW_COUNT,), sample=SampleSpec("system", Decimal("0.05"), seed=7)
    )
    bern = ColumnAggregate(
        ORDERS, None, (Metric.ROW_COUNT,), sample=SampleSpec("bernoulli", Decimal("0.05"), seed=7)
    )
    assert "FROM orders TABLESAMPLE 5% (bernoulli, 7)" in render(bern, DUCK).sql_template
    with pytest.raises(RenderError, match="FL506"):
        render(plan, DUCK)
    assert "FROM orders TABLESAMPLE SYSTEM (5) REPEATABLE (7)" in render(plan, PG).sql_template
    tiny = ColumnAggregate(
        ORDERS,
        None,
        (Metric.ROW_COUNT,),
        sample=SampleSpec("bernoulli", Decimal("0.0000125"), seed=0),
    )
    assert "TABLESAMPLE 0.00125% (bernoulli, 0)" in render(tiny, DUCK).sql_template


def test_unsupported_sample_method_and_seed_range_are_fl506() -> None:
    plan = ColumnAggregate(
        ORDERS, None, (Metric.ROW_COUNT,), sample=SampleSpec("system", Decimal("0.5"))
    )
    with pytest.raises(RenderError, match="FL506"):
        render(plan, with_(DUCK, sample_styles={"system": None}))
    negative = ColumnAggregate(
        ORDERS, None, (Metric.ROW_COUNT,), sample=SampleSpec("system", Decimal("0.5"), seed=-1)
    )
    with pytest.raises(RenderError, match="FL506"):
        render(negative, with_(DUCK, sample_styles={"system": "TABLESAMPLE {percent}%"}))
    assert "REPEATABLE (-1)" in render(negative, PG).sql_template
    keyed = ColumnAggregate(
        ORDERS,
        None,
        (Metric.ROW_COUNT,),
        sample=SampleSpec("keyhash", Decimal("0.5"), key=(ID,)),
    )
    with pytest.raises(RenderError, match="FL506"):
        render(keyed, with_(DUCK, keyhash_expr=None))


def test_fl405_surfaces_from_render() -> None:
    plan = ValueMembership(ORDERS, STATUS, (), sample=SampleSpec("system", Decimal("0.5")))
    with pytest.raises(RenderError, match="FL405"):
        render(plan, DUCK)


# -- catalog columns ------------------------------------------------------------------------


def test_catalog_columns_bind_names_as_string_params() -> None:
    probe = render(MetadataQuery(SHOP_ORDERS, Ident("Status")), PG)
    sql = probe.sql_template
    assert "information_schema.columns" in sql
    values = {p.value for p in probe.params}
    # Postgres folds unquoted names to lower case; the quoted table keeps its case
    assert values == {"wh", "shop", "Orders", "status"}
    assert all(p.family == "string" for p in probe.params)
    for v in values:
        assert v not in sql.replace("information_schema", "")
    duck = render(MetadataQuery(ORDERS), DUCK)
    assert "LOWER(table_name) = LOWER({{" in duck.sql_template
    assert "table_schema = current_schema()" in duck.sql_template
    assert "table_catalog = current_database()" in duck.sql_template
    # Postgres resolves an unqualified table through the search_path
    assert (
        "table_schema = ANY (current_schemas(false))"
        in render(MetadataQuery(ORDERS), PG).sql_template
    )


@pytest.mark.parametrize(
    ("mode", "want_sql", "want_value"),
    [
        ("exact", "column_name = {{cat2}}", "MiXed"),
        ("lower", "column_name = {{cat2}}", "mixed"),
        ("upper", "column_name = {{cat2}}", "MIXED"),
        ("casefold", "LOWER(column_name) = LOWER({{cat2}})", "MiXed"),
    ],
)
def test_catalog_match_modes(mode: str, want_sql: str, want_value: str) -> None:
    for quoted, field in ((True, "quoted_catalog_match"), (False, "unquoted_catalog_match")):
        p = with_(DUCK, **{field: mode})
        probe = render(MetadataQuery(ORDERS, Ident("MiXed", quoted=quoted)), p)
        assert want_sql in probe.sql_template
        assert probe.params[1].value == want_value


def test_catalog_params_never_clash_with_plan_params() -> None:
    p = Printer(DUCK)
    sel = A.Select(
        (A.Aliased(A.ParamRef(Param("cat1", "x", "string")), "x"),),
        A.Join(
            A.SubqueryFrom(
                A.Select((A.Aliased(A.Literal(1), "y"),), A.CatalogColumns(ORDERS)), "c"
            ),
            A.TableName(ORDERS),
            None,
            "CROSS",
        ),
    )
    sql = p.statement(sel)
    names = [x.name for x in p.params]
    assert len(names) == len(set(names)) and "cat1" in names
    assert sql.count("{{cat1}}") == 1


def test_print_select_reports_params_in_first_use_order() -> None:
    plan = PatternCoverage(
        ORDERS, STATUS, "%x%", filter=FCompare(ID, "=", Param("q", 1, "integer"))
    )
    sql, params = print_select(lower(plan), DUCK)
    assert [p.name for p in params] == ["p2", "p1"]  # LIKE pattern (p2) is printed first
    assert sql.index("{{p2}}") < sql.index("{{p1}}")


# -- fragments --------------------------------------------------------------------------------


def test_render_filter_unknown_dialect_is_fl504() -> None:
    f = FCompare(STATUS, "=", Param("p1", "x", "string"))
    with pytest.raises(RenderError, match="FL504"):
        render_filter(f, dialect="nope")
    frag = render_filter(f, dialect="postgres", param_prefix="f")
    assert frag.sql_template == "status = CAST({{f1}} AS TEXT)"


def test_render_join_shapes() -> None:
    from factlang.semantics.refs import EdgeRef

    items = TableRef(None, None, Ident("order_items"))
    edge = EdgeRef(ColumnRef(items, Ident("order_id")), ColumnRef(ORDERS, ID))
    frag = render_join([edge], "duckdb", aliases={items: "i", ORDERS: "o"})
    assert frag.sql_template == "FROM order_items i INNER JOIN orders o ON i.order_id = o.id"
    assert frag.params == ()


# -- type families ------------------------------------------------------------------------------


def test_family_of_unwraps_and_folds_scale_zero_decimals() -> None:
    assert family_of("Nullable(LowCardinality(VARCHAR))", DUCK) == "string"
    assert family_of("DECIMAL(18,0)", DUCK) == "integer"
    assert family_of("numeric(10)", PG) == "integer"
    assert family_of("numeric(10, 2)", PG) == "decimal"
    assert family_of("  bigint ", PG) == "integer"
    assert family_of("geometry", PG) == "other"
