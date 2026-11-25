"""LLM tool definitions and ``handle_tool_call`` (spec 10, agent surface)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any

import pytest

from factlang.agent import TOOL_NAMES, handle_tool_call, tool_definitions, verb_reference
from factlang.agent import tools as agent_tools
from factlang.connectors import CallableConnector, DBAPIConnector
from factlang.engine import Checker
from factlang.ir import ColumnAggregate, Metric
from factlang.judge import Compare
from factlang.packs.standard import standard_pack
from factlang.semantics.catalog import NullCatalog
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import Operand
from factlang.sql import native_dialects

DRAFT_2020_12 = "https://json-schema.org/draft/2020-12/schema"


# -- tool definitions ------------------------------------------------------------------------


def test_three_tools_with_stable_names_and_shape() -> None:
    tools = tool_definitions()
    assert [t["name"] for t in tools] == ["factlang_check", "factlang_lint", "factlang_explain"]
    assert list(TOOL_NAMES) == [t["name"] for t in tools]
    for t in tools:
        assert set(t) == {"name", "description", "input_schema"}
        schema = t["input_schema"]
        assert schema["$schema"] == DRAFT_2020_12
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert schema["properties"]["claims"]["type"] == "string"
        assert "claims" in schema["required"]
        assert isinstance(t["description"], str) and t["description"]
    json.dumps(tools)  # plain JSON


def test_explain_requires_a_dialect() -> None:
    explain = tool_definitions()[2]["input_schema"]
    assert explain["required"] == ["claims", "dialect"]
    assert set(explain["properties"]) == {"claims", "dialect"}


def test_dialect_is_an_enum_of_native_dialects_without_sqlglot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_tools, "sqlglot_available", lambda: False)
    dialect = tool_definitions()[2]["input_schema"]["properties"]["dialect"]
    assert dialect["type"] == "string"
    assert dialect["enum"] == native_dialects()


def test_dialect_is_a_free_string_with_sqlglot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_tools, "sqlglot_available", lambda: True)
    dialect = tool_definitions()[2]["input_schema"]["properties"]["dialect"]
    assert dialect["type"] == "string"
    assert "enum" not in dialect
    assert dialect["examples"] == native_dialects()
    assert "sqlglot" in dialect["description"]


def test_check_description_embeds_the_compact_reference() -> None:
    pack = standard_pack()
    check = tool_definitions(pack)[0]
    assert verb_reference(pack, style="compact") in check["description"]
    lint = tool_definitions(pack)[1]
    assert "factlang_check" in lint["description"]


def test_custom_pack_reaches_the_check_description() -> None:
    pack = standard_pack().extend("mine", "1.0")

    @pack.verb("POSITIVE", summary="All values > 0.", operands=[Operand.column()])
    def positive(claim: CheckedClaim) -> ColumnAggregate:
        return ColumnAggregate(claim.table, claim.column, (Metric.MIN_VALUE,), filter=claim.filter)

    @positive.derive
    def _(payload: Any, claim: CheckedClaim) -> dict[str, object]:
        return {"min": payload["min_value"]}

    positive.decide(Compare("min", ">", value=0))
    positive.facts("min")
    positive.example("POSITIVE orders.total", valid=True)
    assert "POSITIVE orders.total" in tool_definitions(pack)[0]["description"]
    assert "POSITIVE" not in tool_definitions()[0]["description"]


# -- handle_tool_call: errors never raise ----------------------------------------------------


def _offline() -> Checker:
    return Checker(dialect="duckdb", catalog=NullCatalog())


@pytest.mark.parametrize(
    ("name", "arguments", "code"),
    [
        ("factlang_drop", {"claims": "ROWS orders VALUE >= 1"}, "unknown_tool"),
        ("", {}, "unknown_tool"),
        ("factlang_check", None, "invalid_arguments"),
        ("factlang_check", ["ROWS orders VALUE >= 1"], "invalid_arguments"),
        ("factlang_check", {}, "invalid_arguments"),
        ("factlang_check", {"claims": 3}, "invalid_arguments"),
        ("factlang_lint", {"claims": "ROWS t VALUE >= 1", "extra": 1}, "invalid_arguments"),
        ("factlang_explain", {"claims": "ROWS t VALUE >= 1"}, "invalid_arguments"),
        ("factlang_explain", {"claims": "ROWS t VALUE >= 1", "dialect": 5}, "invalid_arguments"),
        ("factlang_explain", {"claims": "ROWS t VALUE >= 1", "dialect": "nope"}, "unknown_dialect"),
        ("factlang_check", {"claims": "ROWS t VALUE >= 1"}, "no_connector"),
    ],
)
def test_errors_are_results_with_stable_codes(name: str, arguments: Any, code: str) -> None:
    result = handle_tool_call(name, arguments, _offline())
    assert set(result) == {"error"}
    error = result["error"]
    assert isinstance(error, dict)
    assert error["code"] == code
    assert isinstance(error["message"], str) and error["message"]
    json.dumps(result)


def test_unexpected_exceptions_become_internal_errors() -> None:
    class Broken(Checker):
        def check(self, text: str) -> Any:
            raise RuntimeError("boom")

    broken = Broken(CallableConnector(lambda sql, params: [], "duckdb"), catalog=NullCatalog())
    result = handle_tool_call("factlang_check", {"claims": "ROWS t VALUE >= 1"}, broken)
    assert result["error"]["code"] == "internal_error"  # type: ignore[index]
    assert "boom" in result["error"]["message"]  # type: ignore[index]


# -- round trip on DuckDB --------------------------------------------------------------------


@pytest.fixture()
def checker() -> Iterator[Checker]:
    duckdb = pytest.importorskip("duckdb")
    db = duckdb.connect()
    db.execute(
        """
        CREATE TABLE customers(id INT, email VARCHAR);
        INSERT INTO customers VALUES (1, 'a@x.io'), (2, 'b@x.io'), (3, NULL);
        CREATE TABLE orders(id INT, customer_id INT, status VARCHAR, total DECIMAL(10,2));
        INSERT INTO orders VALUES (1, 1, 'paid', 5.00), (2, 1, 'open', 7.50),
          (3, 2, 'paid', NULL), (4, 9, 'void', 1.25);
        """
    )
    yield Checker(DBAPIConnector(db, "duckdb"))
    db.close()


def _roundtrip(result: dict[str, object]) -> dict[str, Any]:
    out: dict[str, Any] = json.loads(json.dumps(result))
    return out


def test_check_round_trip(checker: Checker) -> None:
    claims = (
        "ROWS orders VALUE == 4\n"
        "UNIQUE orders.id\n"
        "JOIN orders.customer_id -> customers.id MATCH >= 99%\n"
        "VALUES orders.status IN ['paid', 'open', 'void']\n"
        "NULLS customers.email RATE <= 50%\n"
        "ROWZ orders VALUE == 4\n"
    )
    result = _roundtrip(handle_tool_call("factlang_check", {"claims": claims}, checker))
    expected = _roundtrip(checker.check(claims).to_dict())
    for report in (result, expected):  # timings differ between runs
        for o in report["outcomes"]:
            if o["evidence"] is not None:
                o["evidence"]["duration_ms"] = None
    assert result == expected
    verdicts = [o["verdict"] for o in result["outcomes"]]
    assert verdicts == ["pass", "pass", "fail", "pass", "pass", "invalid"]
    assert result["ok"] is False
    assert next(iter(result)) == "verified" and result["verified"] is False
    assert result["summary"]["pass"] == 4
    invalid = result["outcomes"][5]
    assert any(d["code"] == "FL300" and d["suggestion"] == "ROWS" for d in invalid["diagnostics"])


def test_lint_round_trip(checker: Checker) -> None:
    ok = _roundtrip(
        handle_tool_call(
            "factlang_lint", {"claims": "UNIQUE orders.id\nROWS orders VALUE >= 1"}, checker
        )
    )
    assert ok["ok"] is True
    assert ok["claims"] == 2
    assert [d for d in ok["diagnostics"] if d["severity"] == "error"] == []

    bad = _roundtrip(
        handle_tool_call(
            "factlang_lint", {"claims": "UNIQE orders.id\nROWS orders VALUE >= x"}, checker
        )
    )
    assert bad["ok"] is False
    assert bad["claims"] == 0
    codes = [d["code"] for d in bad["diagnostics"]]
    assert "FL300" in codes and "FL305" in codes
    assert "^" in bad["rendered"]


def test_lint_never_touches_the_warehouse(checker: Checker) -> None:
    class Spy(DBAPIConnector):
        def execute(self, probe: Any, *, timeout_s: float | None) -> Any:
            raise AssertionError("lint executed SQL")

    assert checker.connector is not None
    spy = Checker(Spy(checker.connector.connection, "duckdb"))  # type: ignore[attr-defined]
    result = handle_tool_call("factlang_lint", {"claims": "ROWS nowhere VALUE >= 1"}, spy)
    assert result["ok"] is True


def test_explain_round_trip(checker: Checker) -> None:
    claims = "ROWS orders VALUE == 4\nFOO bar\nUNIQUE orders.id WHERE status = 'paid'"
    result = _roundtrip(
        handle_tool_call("factlang_explain", {"claims": claims, "dialect": "duckdb"}, checker)
    )
    assert result["dialect"] == "duckdb"
    kinds = [e["kind"] for e in result["claims"]]
    assert kinds == ["probe", "outcome", "probe"]
    first, invalid, third = result["claims"]
    assert "orders" in first["sql"] and first["tier"]
    assert invalid["outcome"]["verdict"] == "invalid"
    assert "'paid'" in third["sql"]  # display SQL inlines the literal
    assert [p["value"] for p in third["params"]] == ["paid"]
    name = third["params"][0]["name"]
    assert "{{" + name + "}}" in third["sql_template"]
    assert third["params"][0]["family"] == "string"

    # the displayed SQL is real: it runs on the same DuckDB and returns rows
    assert checker.connector is not None
    db = checker.connector.connection  # type: ignore[attr-defined]
    assert db.execute(first["sql"]).fetchall()


def test_explain_other_dialect_reuses_the_pack(checker: Checker) -> None:
    other = next(d for d in native_dialects() if d != "duckdb")
    result = _roundtrip(
        handle_tool_call(
            "factlang_explain", {"claims": "ROWS orders VALUE == 4", "dialect": other}, checker
        )
    )
    assert result["dialect"] == other
    assert result["claims"][0]["kind"] == "probe"
    assert result["claims"][0]["dialect"] == other


def test_explain_falls_back_to_the_template_for_an_uninlinable_literal(checker: Checker) -> None:
    claims = "COUNT orders VALUE == 0 WHERE status = 'a\\b'"
    result = _roundtrip(
        handle_tool_call("factlang_explain", {"claims": claims, "dialect": "mysql"}, checker)
    )
    (entry,) = result["claims"]
    assert entry["kind"] == "probe"
    assert entry["sql"] == entry["sql_template"] and "{{p1}}" in entry["sql"]
    assert [p["value"] for p in entry["params"]] == ["a\\b"]


# -- claim cap (0.1.3) -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "n"),
    [
        ("", 0),
        ("\n  \n", 0),
        ("# only a comment\n   # another", 0),
        ("USE shop\nUNIQUE orders.id", 1),
        ("UNIQUE orders.id  # trailing comment\nCOUNT orders = 3", 2),
        ("COUNT orders \\\n  = 3\nUNIQUE orders.id", 2),  # a continuation joins lines
        ("COUNT orders \\   \n= 3", 1),  # trailing blanks after the backslash
        ("USE a \\\nUNIQUE x.y", 0),  # joined, the line starts with USE
        ("# note \\\nUNIQUE orders.id", 1),  # a backslash in a comment continues nothing
        ("UNIQUE orders.id # x \\\nUNIQUE orders.id", 2),
        ("COUNT orders WHERE a = '#'\nUNIQUE orders.id", 2),  # # inside a string
        ("UNIQUE orders.id \\", 1),  # dangling continuation at the end
        ("UNIQUE a.b\r\nUNIQUE a.c\r\n", 2),
        ("UNIQUE a.b\rUNIQUE a.c\rUNIQUE a.d", 3),  # a bare CR ends a line, as in the lexer
        ("COUNT orders \\\r= 3\rUNIQUE a.b", 2),  # a continuation before a bare CR
    ],
)
def test_count_claims_follows_the_line_rule(text: str, n: int) -> None:
    assert agent_tools.count_claims(text) == n


def _many(n: int) -> str:
    return "\n".join("UNIQUE orders.id" for _ in range(n))


def test_default_cap_is_200_and_the_boundary_passes() -> None:
    assert agent_tools.DEFAULT_MAX_CLAIMS == 200
    checker = Checker(dialect="duckdb")
    ok = handle_tool_call("factlang_explain", {"claims": _many(200), "dialect": "duckdb"}, checker)
    assert "error" not in ok
    over = handle_tool_call(
        "factlang_explain", {"claims": _many(201), "dialect": "duckdb"}, checker
    )
    assert over["error"]["code"] == "too_many_claims"  # type: ignore[index]
    assert over["error"]["tool"] == "factlang_explain"  # type: ignore[index]
    assert "201" in over["error"]["message"] and "200" in over["error"]["message"]  # type: ignore[index]


def test_cap_applies_to_check_before_the_connector_or_any_sql() -> None:
    calls: list[str] = []
    conn = CallableConnector(lambda sql, params: calls.append(sql) or [], "duckdb")
    out = handle_tool_call(
        "factlang_check", {"claims": _many(5)}, Checker(conn, dialect="duckdb"), max_claims=4
    )
    assert out["error"]["code"] == "too_many_claims"  # type: ignore[index]
    assert calls == []
    # no connector: the cap is reported first for an oversize call, no_connector otherwise
    bare = Checker(dialect="duckdb")
    assert (
        handle_tool_call("factlang_check", {"claims": _many(5)}, bare, max_claims=4)["error"][  # type: ignore[index]
            "code"
        ]
        == "too_many_claims"
    )
    assert (
        handle_tool_call("factlang_check", {"claims": _many(4)}, bare, max_claims=4)["error"][  # type: ignore[index]
            "code"
        ]
        == "no_connector"
    )


@pytest.mark.parametrize("tool", ["factlang_check", "factlang_explain"])
def test_bare_cr_line_endings_do_not_bypass_the_cap(tool: str) -> None:
    calls: list[str] = []
    conn = CallableConnector(lambda sql, params: calls.append(sql) or [], "duckdb")
    claims = "\r".join("UNIQUE orders.id" for _ in range(300))
    args = {"claims": claims, **({"dialect": "duckdb"} if tool == "factlang_explain" else {})}
    out = handle_tool_call(tool, args, Checker(conn))
    assert out["error"]["code"] == "too_many_claims"  # type: ignore[index]
    assert "300" in out["error"]["message"]  # type: ignore[index]
    assert calls == []


def test_the_cap_counts_before_analysis_even_for_invalid_claims() -> None:
    garbage = "\n".join("BOGUS ???" for _ in range(3))
    out = handle_tool_call(
        "factlang_explain",
        {"claims": garbage, "dialect": "duckdb"},
        Checker(dialect="duckdb"),
        max_claims=2,
    )
    assert out["error"]["code"] == "too_many_claims"  # type: ignore[index]


def test_lint_is_never_capped_and_none_removes_the_cap() -> None:
    checker = Checker(dialect="duckdb")
    lint = handle_tool_call("factlang_lint", {"claims": _many(300)}, checker, max_claims=1)
    assert "error" not in lint and lint["claims"] == 300
    free = handle_tool_call(
        "factlang_explain", {"claims": _many(300), "dialect": "duckdb"}, checker, max_claims=None
    )
    assert "error" not in free


def test_too_many_claims_is_a_listed_error_code() -> None:
    assert "too_many_claims" in agent_tools.ERROR_CODES
