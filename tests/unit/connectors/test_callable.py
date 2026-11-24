"""CallableConnector: the host runs SQL itself (spec 9.1)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from factlang.connectors import CallableConnector, ExecutionError, QueryResult, aexecute
from factlang.ir import ColumnAggregate, Metric
from factlang.semantics.filters import FCompare, Param
from factlang.semantics.refs import Ident, TableRef
from factlang.sql import get_profile, render

DUCK = get_profile("duckdb")
assert DUCK is not None
ORDERS = TableRef(None, None, Ident("orders"))


def probe() -> Any:
    plan = ColumnAggregate(
        ORDERS,
        None,
        (Metric.ROW_COUNT,),
        filter=FCompare(Ident("status"), "=", Param("p1", "paid", "string")),
    )
    return render(plan, DUCK)


def test_calls_fn_with_bound_sql_and_params() -> None:
    seen: list[tuple[str, object]] = []

    def fn(sql: str, params: tuple[object, ...] | dict[str, object]) -> list[dict[str, Any]]:
        seen.append((sql, params))
        return [{"row_count": 3}]

    conn = CallableConnector(fn, "duckdb")
    assert conn.paramstyle == "qmark"
    result = conn.execute(probe(), timeout_s=None)
    assert result == QueryResult([{"row_count": 3}])
    sql, params = seen[0]
    assert "?" in sql and "paid" not in sql
    assert params == ("paid",)


def test_named_paramstyle_gives_a_dict() -> None:
    seen: list[object] = []

    def fn(sql: str, params: tuple[object, ...] | dict[str, object]) -> list[dict[str, Any]]:
        seen.append(params)
        assert ":p1" in sql
        return []

    CallableConnector(fn, "duckdb", paramstyle="named").execute(probe(), timeout_s=1)
    assert seen == [{"p1": "paid"}]


def test_unknown_paramstyle_rejected() -> None:
    with pytest.raises(ValueError):
        CallableConnector(lambda s, p: [], "duckdb", paramstyle="dollar")


def test_rows_are_copied_to_dicts() -> None:
    class Row(dict[str, Any]):
        pass

    result = CallableConnector(lambda s, p: [Row(a=1)], "duckdb").execute(probe(), timeout_s=None)
    assert type(result.rows[0]) is dict


def test_bad_return_is_fl700() -> None:
    conn = CallableConnector(lambda s, p: "nope", "duckdb")  # type: ignore[arg-type,return-value]
    with pytest.raises(ExecutionError) as info:
        conn.execute(probe(), timeout_s=None)
    assert info.value.code == "FL700"
    conn = CallableConnector(lambda s, p: [1], "duckdb")  # type: ignore[list-item]
    with pytest.raises(ExecutionError):
        conn.execute(probe(), timeout_s=None)


def test_host_error_is_fl700_and_chained() -> None:
    def fn(sql: str, params: object) -> list[dict[str, Any]]:
        raise RuntimeError("relation does not exist")

    with pytest.raises(ExecutionError) as info:
        CallableConnector(fn, "duckdb").execute(probe(), timeout_s=None)  # type: ignore[arg-type]
    assert info.value.code == "FL700"
    assert "relation does not exist" in str(info.value)
    assert isinstance(info.value.__cause__, RuntimeError)


def test_host_timeout_is_fl702() -> None:
    def fn(sql: str, params: object) -> list[dict[str, Any]]:
        raise TimeoutError("statement timeout")

    with pytest.raises(ExecutionError) as info:
        CallableConnector(fn, "duckdb").execute(probe(), timeout_s=1)  # type: ignore[arg-type]
    assert info.value.code == "FL702"


def test_host_execution_error_passes_through() -> None:
    def fn(sql: str, params: object) -> list[dict[str, Any]]:
        raise ExecutionError("FL701 over budget")

    with pytest.raises(ExecutionError) as info:
        CallableConnector(fn, "duckdb").execute(probe(), timeout_s=1)  # type: ignore[arg-type]
    assert info.value.code == "FL701"
    assert info.value.__cause__ is None  # passed through, not re-wrapped


def test_aexecute_runs_a_sync_connector_in_a_thread() -> None:
    conn = CallableConnector(lambda s, p: [{"row_count": 1}], "duckdb")
    result = asyncio.run(aexecute(conn, probe(), timeout_s=None))
    assert result.rows == [{"row_count": 1}]


def test_aexecute_prefers_the_native_coroutine() -> None:
    class Native:
        dialect = "duckdb"
        paramstyle = "qmark"

        def execute(self, probe: Any, *, timeout_s: float | None) -> QueryResult:
            raise AssertionError("sync path used")

        async def aexecute(self, probe: Any, *, timeout_s: float | None) -> QueryResult:
            return QueryResult([{"x": timeout_s}])

    result = asyncio.run(aexecute(Native(), probe(), timeout_s=2.0))
    assert result.rows == [{"x": 2.0}]
