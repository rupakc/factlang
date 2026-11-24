"""SQLAlchemyConnector and connect() with a fake ``sqlalchemy`` module (spec 9.1)."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from factlang.connectors import ExecutionError, SQLAlchemyConnector, connect, run_metadata
from factlang.ir import ColumnAggregate, Metric
from factlang.semantics.filters import FCompare, Param
from factlang.semantics.refs import Ident, TableRef
from factlang.sql import RenderedProbe, get_profile, render

DUCK = get_profile("duckdb")
assert DUCK is not None


def probe() -> Any:
    plan = ColumnAggregate(
        TableRef(None, None, Ident("orders")),
        None,
        (Metric.ROW_COUNT,),
        filter=FCompare(Ident("status"), "=", Param("p1", "paid", "string")),
    )
    return render(plan, DUCK)


class TextClause:
    def __init__(self, sql: str) -> None:
        self.sql = sql


class Row:
    def __init__(self, mapping: dict[str, Any]) -> None:
        self._mapping = mapping


class FakeDBAPIConn:
    def __init__(self) -> None:
        self.interrupted = 0

    def interrupt(self) -> None:
        self.interrupted += 1


class FakeConnection:
    def __init__(self, engine: FakeEngine) -> None:
        self.engine = engine
        self.connection = types.SimpleNamespace(dbapi_connection=engine.raw)

    def __enter__(self) -> FakeConnection:
        return self

    def __exit__(self, *exc: object) -> None:
        self.engine.closed += 1

    def execute(self, clause: TextClause, params: dict[str, Any]) -> list[Row]:
        self.engine.log.append((clause.sql, params))
        if self.engine.raise_exc is not None:
            raise self.engine.raise_exc
        return [Row({"row_count": 2})]


class FakeEngine:
    def __init__(self, name: str) -> None:
        self.dialect = types.SimpleNamespace(name=name)
        self.log: list[tuple[str, Any]] = []
        self.raise_exc: BaseException | None = None
        self.closed = 0
        self.raw = FakeDBAPIConn()

    def connect(self) -> FakeConnection:
        return FakeConnection(self)


@pytest.fixture()
def fake_sqlalchemy(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    mod = types.ModuleType("sqlalchemy")
    mod.text = TextClause  # type: ignore[attr-defined]
    created: list[str] = []

    def create_engine(url: str) -> FakeEngine:
        created.append(url)
        return FakeEngine(url.split(":", 1)[0].split("+", 1)[0])

    mod.create_engine = create_engine  # type: ignore[attr-defined]
    mod.created = created  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sqlalchemy", mod)
    return mod


@pytest.mark.parametrize(
    ("name", "dialect"),
    [
        ("postgresql", "postgres"),
        ("mssql", "tsql"),
        ("mariadb", "mysql"),
        ("mysql", "mysql"),
        ("snowflake", "snowflake"),
        ("duckdb", "duckdb"),
    ],
)
def test_dialect_mapping(fake_sqlalchemy: types.ModuleType, name: str, dialect: str) -> None:
    c = SQLAlchemyConnector(FakeEngine(name))
    assert c.dialect == dialect
    assert c.paramstyle == "named"


def test_executes_text_with_named_params(fake_sqlalchemy: types.ModuleType) -> None:
    engine = FakeEngine("duckdb")
    result = SQLAlchemyConnector(engine).execute(probe(), timeout_s=None)
    assert result.rows == [{"row_count": 2}]
    sql, params = engine.log[0]
    assert ":p1" in sql and "paid" not in sql
    assert params == {"p1": "paid"}
    assert engine.closed == 1


def test_colons_are_escaped_for_text(fake_sqlalchemy: types.ModuleType) -> None:
    engine = FakeEngine("postgresql")
    p = RenderedProbe(
        "SELECT 'a:b' AS x, y::text AS row_count FROM t WHERE z = {{p}}",
        (Param("p", "v", "string"),),
        probe().arity,
        probe().plan,
        "postgres",
        "verified",
    )
    SQLAlchemyConnector(engine).execute(p, timeout_s=None)
    sql, params = engine.log[0]
    assert sql == "SELECT 'a\\:b' AS x, y\\:\\:text AS row_count FROM t WHERE z = :p"
    assert params == {"p": "v"}


@pytest.mark.parametrize(
    "template",
    [
        "SELECT a{{t}} AS row_count",
        "SELECT {{t}}b AS row_count",
        "SELECT {{t}}{{u}} AS row_count",
        "SELECT x::{{t}} AS row_count",
        "SELECT x:{{t}} AS row_count",
    ],
)
def test_glued_tokens_are_refused(fake_sqlalchemy: types.ModuleType, template: str) -> None:
    engine = FakeEngine("postgresql")
    names = [n for n in ("t", "u") if "{{" + n + "}}" in template]
    p = RenderedProbe(
        template,
        tuple(Param(n, "v", "string") for n in names),
        probe().arity,
        probe().plan,
        "postgres",
        "verified",
    )
    with pytest.raises(ExecutionError) as info:
        SQLAlchemyConnector(engine).execute(p, timeout_s=None)
    assert info.value.code == "FL700"
    assert engine.log == []


def test_separated_tokens_bind(fake_sqlalchemy: types.ModuleType) -> None:
    engine = FakeEngine("postgresql")
    p = RenderedProbe(
        "SELECT ({{t}}) AS row_count, x = {{u}}",
        (Param("t", 1, "integer"), Param("u", "v", "string")),
        probe().arity,
        probe().plan,
        "postgres",
        "verified",
    )
    SQLAlchemyConnector(engine).execute(p, timeout_s=None)
    assert engine.log == [("SELECT (:t) AS row_count, x = :u", {"t": 1, "u": "v"})]


def test_token_right_after_colon_is_refused(fake_sqlalchemy: types.ModuleType) -> None:
    engine = FakeEngine("postgresql")
    p = RenderedProbe(
        "SELECT x::{{t}} AS row_count FROM t",
        (Param("t", "v", "string"),),
        probe().arity,
        probe().plan,
        "postgres",
        "verified",
    )
    with pytest.raises(ExecutionError) as info:
        SQLAlchemyConnector(engine).execute(p, timeout_s=None)
    assert info.value.code == "FL700"
    assert engine.log == []  # refused before executing


def test_real_sqlalchemy_keeps_colons_in_literals() -> None:
    sa = pytest.importorskip("sqlalchemy")
    engine = sa.create_engine("sqlite://")
    c = SQLAlchemyConnector(engine)
    result = run_metadata(c, "SELECT 'a:b' AS x, {{p}} AS y", {"p": "q:r"})
    assert result.rows == [{"x": "a:b", "y": "q:r"}]
    upper = RenderedProbe(
        "SELECT 'a:b' AS ROW_COUNT", (), probe().arity, probe().plan, "sqlite", "best_effort"
    )
    assert c.execute(upper, timeout_s=None).rows == [{"row_count": "a:b"}]


def test_errors_are_mapped(fake_sqlalchemy: types.ModuleType) -> None:
    engine = FakeEngine("postgresql")
    engine.raise_exc = RuntimeError("relation does not exist")
    with pytest.raises(ExecutionError) as info:
        SQLAlchemyConnector(engine).execute(probe(), timeout_s=None)
    assert info.value.code == "FL700"
    engine.raise_exc = RuntimeError("canceling statement due to statement timeout")
    with pytest.raises(ExecutionError) as info:
        SQLAlchemyConnector(engine).execute(probe(), timeout_s=1)
    assert info.value.code == "FL702"


def test_timeout_interrupts_the_raw_connection(fake_sqlalchemy: types.ModuleType) -> None:
    import threading

    engine = FakeEngine("duckdb")
    released = threading.Event()

    def slow_execute(self: FakeConnection, clause: TextClause, params: Any) -> list[Row]:
        # stands in for a driver that raises once interrupted
        for _ in range(200):
            if engine.raw.interrupted:
                raise RuntimeError("INTERRUPT Error: Interrupted!")
            released.wait(0.01)
        return []

    original = FakeConnection.execute
    FakeConnection.execute = slow_execute  # type: ignore[method-assign]
    try:
        with pytest.raises(ExecutionError) as info:
            SQLAlchemyConnector(engine).execute(probe(), timeout_s=0.05)
    finally:
        FakeConnection.execute = original  # type: ignore[method-assign]
    assert info.value.code == "FL702"
    assert engine.raw.interrupted == 1


def test_connect_builds_from_a_url(fake_sqlalchemy: types.ModuleType) -> None:
    c = connect("postgresql+psycopg://u@h/db")
    assert isinstance(c, SQLAlchemyConnector)
    assert c.dialect == "postgres"
    assert fake_sqlalchemy.created == ["postgresql+psycopg://u@h/db"]  # type: ignore[attr-defined]


def test_connect_without_sqlalchemy_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "sqlalchemy", None)
    with pytest.raises(ImportError, match=r"factlang\[sqlalchemy\]"):
        connect("duckdb:///:memory:")
