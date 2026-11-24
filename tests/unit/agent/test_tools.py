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
