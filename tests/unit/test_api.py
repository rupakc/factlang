"""The public facade ``import factlang as fl`` (spec 10, 11 R10, 18 R1/R6)."""

from __future__ import annotations

import importlib
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

import factlang as fl
from factlang.diagnostics import DiagnosticBag
from factlang.engine import CostEstimate, Measurement
from factlang.semantics.catalog import StaticCatalog
from factlang.semantics.filters import FBool, FCompare, FIsNull, FNot, FOr
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef
from factlang.sql import RenderedFragment, RenderedProbe
from factlang.syntax.parser import ParseResult

# R10: the stability contract. Adding a name is a minor change; removing or renaming one
# is a breaking change and must fail this test first.
EXPORTS = {
    "parse",
    "lint",
    "format",
    "compile",
    "check",
    "Checker",
    "Report",
    "Outcome",
    "Verdict",
    "standard_pack",
    "ContractPack",
    "Contract",
    "Example",
    "Operand",
    "Attr",
    "Compare",
    "CompareUsingClaimOp",
    "Within",
    "And",
    "Or",
    "Not",
    "WhenClaimHas",
    "Metric",
    "ir",
    "sql",
    "types",
    "DBAPIConnector",
    "CallableConnector",
    "SQLAlchemyConnector",
    "BigQueryConnector",
    "connect",
    "StaticCatalog",
    "NullCatalog",
    "CachedCatalog",
    "WarehouseCatalog",
    "__version__",
    "LANGUAGE_VERSION",
    # Amendment A (R1, R5, R6, R8)
    "measure",
    "parse_filter",
    "Measurement",
    "CostEstimate",
    "RenderedFragment",
    "run_metadata",
}


def test_exports_are_exactly_the_stability_contract() -> None:
    assert set(fl.__all__) == EXPORTS
    assert len(fl.__all__) == len(EXPORTS)  # no duplicates
    for name in EXPORTS:
        assert hasattr(fl, name), name


def test_exported_objects_are_the_real_ones() -> None:
    from factlang import connectors, engine, judge, semantics
    from factlang import ir as ir_mod
    from factlang import sql as sql_mod
    from factlang.semantics import types as types_mod
    from factlang.semantics.signature import AttrSpec

    assert fl.Attr is AttrSpec
    assert fl.ir is ir_mod and fl.sql is sql_mod and fl.types is types_mod
    assert fl.Checker is engine.Checker and fl.Report is engine.Report
    assert fl.Measurement is Measurement and fl.CostEstimate is CostEstimate
    assert fl.Outcome is judge.Outcome and fl.Verdict is judge.Verdict
    assert fl.RenderedFragment is RenderedFragment
    assert fl.run_metadata is connectors.run_metadata
    assert fl.NullCatalog is semantics.catalog.NullCatalog
    # R6 / R8: reachable through their packages too
    assert fl.sql.render_filter is sql_mod.render_filter
    assert fl.sql.render_join is sql_mod.render_join
    assert fl.connectors.run_metadata is connectors.run_metadata  # type: ignore[attr-defined]
    assert fl.types.register is types_mod.register_type
    assert fl.__version__ == "0.1.0" and fl.LANGUAGE_VERSION == "1.0"


def test_facade_import_is_idempotent() -> None:
    assert importlib.reload(fl).__all__ == fl.__all__


# --- front end ---------------------------------------------------------------------------


def test_parse_returns_document_and_diagnostics() -> None:
    res = fl.parse("JOIN a.x -> b.y MATCH >=99%")
    assert isinstance(res, ParseResult) and len(res.document.items) == 1
    assert not res.diagnostics.has_errors


def test_lint_reports_front_end_errors_and_renders_without_source() -> None:
    text = "UNIQUE orders.id\nBOGUS orders.id\n"
    bag = fl.lint(text)
    assert isinstance(bag, DiagnosticBag)
    assert bag.codes() == ["FL300"]
    rendered = bag.render()
    assert "BOGUS orders.id" in rendered and "^^^^^" in rendered
    assert fl.lint("UNIQUE orders.id\n").codes() == []


def test_lint_uses_pack_and_catalog() -> None:
    pack = fl.standard_pack().extend("mine", "1.0")
    pack.alias("UNIQ", "UNIQUE")
    assert fl.lint("UNIQ orders.id", pack=pack).codes() == []
    assert fl.lint("UNIQ orders.id").codes() == ["FL300"]
    cat = StaticCatalog({"orders": {"id": "integer"}})
    assert fl.lint("UNIQUE orders.nope", catalog=cat).codes() == ["FL311"]


def test_format_is_canonical_and_refuses_broken_input() -> None:
    assert fl.format("JOIN a.x->b.y MATCH >= 99%") == "JOIN a.x -> b.y MATCH >= 99%\n"
    with pytest.raises(ValueError, match="FL20"):  # a parse error: lines would be dropped
        fl.format("UNIQUE orders.id\nUNIQUE orders.id MATCH\n")
    # an unknown verb is an analysis error, not a syntax error: it still formats
    assert fl.format("BOGUS  orders.id") == "BOGUS orders.id\n"


def test_compile_returns_probes_and_skips_unprobeable() -> None:
    text = "UNIQUE orders.id\nNOTE orders.total IS eur BECAUSE 'finance'\nROWS orders VALUE > 0\n"
    probes = fl.compile(text, dialect="snowflake")
    assert len(probes) == 2 and all(isinstance(p, RenderedProbe) for p in probes)
    assert {p.dialect for p in probes} == {"snowflake"}
    with pytest.raises(ValueError, match="FL300"):
        fl.compile("BOGUS orders.id", dialect="duckdb")
    with pytest.raises(ValueError, match="FL50"):
        fl.compile("UNIQUE orders.id", dialect="no-such-dialect")


# --- parse_filter (R6) -------------------------------------------------------------------


def test_parse_filter_with_or_without_where() -> None:
    f, bag = fl.parse_filter("deleted IS NULL OR NOT deleted")
    assert len(bag) == 0
    assert f == FOr((FIsNull(Ident("deleted"), False), FNot(FBool(Ident("deleted")))))
    g, _ = fl.parse_filter("WHERE deleted IS NULL OR NOT deleted")
    assert g == f
    g, bag = fl.parse_filter("\n\n  deleted IS NULL OR NOT deleted\n\n")
    assert g == f and len(bag) == 0
    h, _ = fl.parse_filter("  status = 'paid'  # comment")
    assert isinstance(h, FCompare) and h.param.name == "p1" and h.param.value == "paid"


@pytest.mark.parametrize(
    ("text", "code"),
    [
        ("", "FL206"),
        ("  # only a comment", "FL206"),
        ("WHERE", "FL206"),
        ("a = 1 b", "FL206"),
        ("a = 1\nb = 2", "FL206"),
        ("a = 'unterminated", "FL101"),
        ("a = DATE '2026-13-45'", "FL309"),
    ],
)
def test_parse_filter_errors(text: str, code: str) -> None:
    f, bag = fl.parse_filter(text)
    assert f is None and code in bag.codes()


def test_parse_filter_checks_columns_with_catalog_and_table() -> None:
    cat = StaticCatalog({"orders": {"status": "varchar", "deleted": "boolean"}})
    t = TableRef(None, None, Ident("orders"))
    f, bag = fl.parse_filter("NOT deleted AND status = 'x'", catalog=cat, table=t)
    assert f is not None and bag.codes() == []
    f, bag = fl.parse_filter("status", catalog=cat, table=t)
    assert f is None and bag.codes() == ["FL305"]
    f, bag = fl.parse_filter("nope = 1", catalog=cat, table=t)
    assert f is None and bag.codes() == ["FL311"]
    # no table: names cannot be checked, the filter still resolves
    f, bag = fl.parse_filter("nope = 1", catalog=cat)
    assert f is not None and bag.codes() == []


def test_parse_filter_hostile_literal_stays_a_parameter() -> None:
    f, _ = fl.parse_filter("status = 'x''; DROP TABLE orders; --'")
    assert f is not None
    frag = fl.sql.render_filter(f, dialect="postgres")
    assert "DROP" not in frag.sql_template
    assert any("DROP" in str(p.value) for p in frag.params)


# --- one-call check and measure (DuckDB) -------------------------------------------------


def _duckdb() -> Any:
    duckdb = pytest.importorskip("duckdb")
    conn = duckdb.connect()
    conn.execute(
        "CREATE TABLE orders (id INTEGER, customer_id INTEGER, amount DECIMAL(10,2), "
        "status VARCHAR, deleted BOOLEAN)"
    )
    conn.execute(
        "INSERT INTO orders VALUES (1, 10, 5.00, 'paid', NULL), (2, 10, 7.50, 'paid', FALSE), "
        "(3, 11, 2.25, 'open', TRUE)"
    )
    conn.execute("CREATE TABLE customers (id INTEGER)")
    conn.execute("INSERT INTO customers VALUES (10), (11)")
    conn.execute("CREATE TABLE order_items (order_id INTEGER, deleted BOOLEAN)")
    conn.execute("INSERT INTO order_items VALUES (1, NULL), (2, FALSE), (3, NULL)")
    return conn


def test_check_one_claim_returns_outcome_many_return_report() -> None:
    conn = _duckdb()
    c = fl.DBAPIConnector(conn, "duckdb")
    out = fl.check("UNIQUE orders.id", connector=c)
    assert isinstance(out, fl.Outcome) and out.verdict is fl.Verdict.PASS
    rep = fl.check("UNIQUE orders.id\nUNIQUE orders.customer_id", connector=c)
    assert isinstance(rep, fl.Report)
    assert [o.verdict for o in rep.outcomes] == [fl.Verdict.PASS, fl.Verdict.FAIL]
    empty = fl.check("", connector=c)
    assert isinstance(empty, fl.Report) and empty.outcomes == ()
    # checker kwargs pass through
    out = fl.check("UNIQUE orders.nope", connector=c, catalog=fl.NullCatalog())
    assert isinstance(out, fl.Outcome) and out.verdict is fl.Verdict.ERROR


def test_measure_one_plan_or_many() -> None:
    conn = _duckdb()
    c = fl.DBAPIConnector(conn, "duckdb")
    t = TableRef(None, None, Ident("orders"))
    plan = fl.ir.ColumnAggregate(t, Ident("amount"), [fl.Metric.MIN_VALUE])
    m = fl.measure(plan, connector=c)
    assert isinstance(m, fl.Measurement) and m.error is None
    assert m.payload is not None and m.payload["min_value"] == Decimal("2.25")
    ms = fl.measure([plan, plan], connector=c)
    assert isinstance(ms, list) and len(ms) == 2
    assert all(x.payload == m.payload for x in ms)


# --- spec section 10, run verbatim on DuckDB --------------------------------------------

# Spec section 10's two code blocks, character for character, except ONE marked line:
# spec 10 types ``parse_filter`` as ``-> tuple[FilterExpr | None, DiagnosticBag]`` while its
# example passes the result straight to ``render_filter``; the tuple is unpacked here.
SPEC_10 = (
    """\
import factlang as fl

# 1. Front end only (no warehouse)
doc = fl.parse("JOIN a.x -> b.y MATCH >=99%")        # Document + diagnostics
print(fl.lint(text).render())                          # caret diagnostics
print(fl.format(text))                                 # canonical form
probe = fl.compile("UNIQUE orders.id", dialect="snowflake")   # sql + params

# 2. One call
outcome = fl.check("UNIQUE orders.id", connector=fl.DBAPIConnector(conn, "duckdb"))

# 3. Batch
checker = fl.Checker(connector, catalog=fl.WarehouseCatalog(connector))
report = checker.check(open("facts.fl").read())

# 4. Extend
pack = fl.standard_pack().extend("mine", "1.0")
@pack.verb("POSITIVE", operands=[fl.Operand.column()], filterable=True)
def positive(claim):                                    # returns the plan
    return fl.ir.ColumnAggregate(claim.table, claim.column, [fl.Metric.MIN_VALUE])
@positive.derive
def _(payload, claim): return {"min": payload["min_value"]}
positive.decide(fl.Compare("min", ">", value=0))
positive.example("POSITIVE orders.amount", valid=True)
"""
    + "\n"
    + """\
m = fl.measure(fl.ir.ValueMembership(t, col, declared=(), report_limit=20), connector=c)  # exact top-k
est = checker.estimate("JOIN orders.customer_id -> customers.id MATCH >=99%")             # dry-run bytes
f, diags = fl.parse_filter("deleted IS NULL OR NOT deleted")     # FilterExpr + diagnostics, no claim  # DEVIATION
frag = fl.sql.render_filter(f, table=t, dialect="snowflake")     # RenderedFragment(sql_template, params)
snippet = fl.sql.render_join([edge1, edge2], dialect="bigquery",
                             filters={t1: f})                     # RenderedFragment, display/bindable
print(frag.inline())                                              # display-only text, literals escaped
"""  # noqa: E501
)


def test_spec_section_10_example_runs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _duckdb()
    monkeypatch.chdir(tmp_path)
    (tmp_path / "facts.fl").write_text(
        "UNIQUE orders.id\nJOIN orders.customer_id -> customers.id MATCH >=99%\n",
        encoding="utf-8",
    )
    connector = fl.DBAPIConnector(conn, "duckdb")
    t = TableRef(None, None, Ident("orders"))
    t1 = TableRef(None, None, Ident("order_items"))
    customers = TableRef(None, None, Ident("customers"))
    ns: dict[str, Any] = {
        "text": "UNIQUE orders.id\n",
        "conn": conn,
        "connector": connector,
        "c": connector,
        "t": t,
        "col": Ident("status"),
        "t1": t1,
        "edge1": EdgeRef(ColumnRef(t1, Ident("order_id")), ColumnRef(t, Ident("id"))),
        "edge2": EdgeRef(ColumnRef(t, Ident("customer_id")), ColumnRef(customers, Ident("id"))),
    }
    exec(compile(SPEC_10, "<spec section 10>", "exec"), ns)

    assert ns["fl"] is fl
    doc, probe, outcome, report = ns["doc"], ns["probe"], ns["outcome"], ns["report"]
    assert isinstance(doc, ParseResult) and not doc.diagnostics.has_errors
    assert len(probe) == 1 and probe[0].dialect == "snowflake"
    assert isinstance(outcome, fl.Outcome) and outcome.verdict is fl.Verdict.PASS
    assert report.ok and [o.verdict for o in report.outcomes] == [fl.Verdict.PASS] * 2
    pack = ns["pack"]
    ext = fl.Checker(connector, pack=pack).check("POSITIVE orders.amount")
    assert [o.verdict for o in ext.outcomes] == [fl.Verdict.PASS]
    assert ext.outcomes[0].facts["min"] == Decimal("2.25")
    m = ns["m"]
    assert isinstance(m, fl.Measurement) and m.error is None and m.payload is not None
    est = ns["est"]
    assert len(est) == 1 and est[0].method == "unknown" and est[0].bytes is None
    f, frag, snippet = ns["f"], ns["frag"], ns["snippet"]
    assert len(ns["diags"]) == 0
    assert isinstance(frag, fl.RenderedFragment) and frag.dialect == "snowflake"
    assert isinstance(snippet, fl.RenderedFragment) and snippet.sql_template.startswith("FROM ")
    # the same join path rendered for duckdb executes and matches a hand-written join
    dd = fl.sql.render_join([ns["edge1"], ns["edge2"]], dialect="duckdb", filters={t1: f})
    sql, params = dd.bind("qmark")
    got = conn.execute(f"SELECT COUNT(*) {sql}", params).fetchone()[0]
    want = conn.execute(
        "SELECT COUNT(*) FROM order_items i JOIN orders o ON i.order_id = o.id "
        "JOIN customers c ON o.customer_id = c.id WHERE i.deleted IS NULL OR NOT i.deleted"
    ).fetchone()[0]
    assert got == want == 3


def test_spec_10_copy_matches_the_spec_when_available() -> None:
    """Drift guard: the copy above is the spec's text (skipped in an sdist without docs)."""
    spec = Path(__file__).resolve().parents[2] / "docs" / "specs" / "2026-11-24-factlang-design.md"
    if not spec.exists():
        pytest.skip("spec not shipped")
    text = spec.read_text(encoding="utf-8")
    original = SPEC_10.replace(
        'f, diags = fl.parse_filter("deleted IS NULL OR NOT deleted")     '
        "# FilterExpr + diagnostics, no claim  # DEVIATION",
        'f = fl.parse_filter("deleted IS NULL OR NOT deleted")            '
        "# FilterExpr + diagnostics, no claim",
    )
    for line in original.splitlines():
        assert line in text.splitlines(), line
