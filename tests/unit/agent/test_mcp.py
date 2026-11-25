"""The optional MCP server (spec 10, extra ``factlang[mcp]``).

The registration data and the argument handling are pure and tested without the ``mcp``
package; the live server tests are skipped when ``mcp`` is not installed.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import subprocess
import sys
import types
from collections.abc import Iterator
from typing import Any

import pytest

from factlang.agent import mcp as fl_mcp
from factlang.agent import tool_definitions
from factlang.connectors import DBAPIConnector
from factlang.engine import Checker
from factlang.packs.standard import standard_pack
from factlang.semantics.catalog import NullCatalog

# -- pure: no ``mcp`` package needed ---------------------------------------------------------


def test_importing_the_module_does_not_import_mcp() -> None:
    code = "import sys, factlang.agent.mcp; print('mcp' in sys.modules)"
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "False"


def test_registrations_mirror_the_tool_definitions() -> None:
    pack = standard_pack()
    regs = fl_mcp.tool_registrations(pack)
    defs = tool_definitions(pack)
    assert [r["name"] for r in regs] == ["factlang_check", "factlang_lint", "factlang_explain"]
    for reg, d in zip(regs, defs, strict=True):
        assert set(reg) == {"name", "description", "inputSchema"}
        assert reg["name"] == d["name"]
        assert reg["description"] == d["description"]
        assert reg["inputSchema"] == d["input_schema"]


def test_call_tool_returns_json_text_and_never_raises() -> None:
    checker = Checker(dialect="duckdb", catalog=NullCatalog())
    text = fl_mcp.call_tool(checker, "factlang_lint", {"claims": "UNIQUE orders.id"})
    assert json.loads(text)["ok"] is True
    err = json.loads(fl_mcp.call_tool(checker, "nope", {}))
    assert err["error"]["code"] == "unknown_tool"
    err = json.loads(fl_mcp.call_tool(checker, "factlang_lint", None))
    assert err["error"]["code"] == "invalid_arguments"


def _without_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    real = importlib.import_module

    def fake(name: str, package: str | None = None) -> Any:
        if name == "mcp" or name.startswith("mcp."):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real(name, package)

    monkeypatch.setattr(fl_mcp.importlib, "import_module", fake)


def test_serve_without_the_extra_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    _without_mcp(monkeypatch)
    with pytest.raises(ImportError, match=r"factlang\[mcp\]"):
        fl_mcp.serve(Checker(dialect="duckdb"))


def test_run_needs_a_dsn_or_a_dialect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("FACTLANG_DSN", raising=False)
    args = fl_mcp.configure_parser(argparse.ArgumentParser()).parse_args([])
    assert fl_mcp.run(args) == 2
    err = capsys.readouterr().err
    assert "--dsn" in err and "FACTLANG_DSN" in err
    monkeypatch.setenv("FACTLANG_DSN", "")  # empty counts as absent
    assert fl_mcp.run(args) == 2


def test_dsn_comes_from_the_environment_when_no_option_is_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from factlang import cli

    assert fl_mcp.DSN_ENV == cli.DSN_ENV == "FACTLANG_DSN"
    served: list[Checker] = []
    connected: list[str] = []
    monkeypatch.setattr(fl_mcp, "serve", lambda checker, **kw: served.append(checker))
    from factlang.connectors import CallableConnector

    def fake_connect(dsn: str) -> CallableConnector:
        connected.append(dsn)
        return CallableConnector(lambda sql, params: [], "postgres")

    monkeypatch.setattr(fl_mcp, "connect", fake_connect)
    monkeypatch.setenv("FACTLANG_DSN", "postgresql://reader@warehouse/shop")
    assert fl_mcp.main([]) == 0
    assert connected == ["postgresql://reader@warehouse/shop"]
    # an explicit option wins over the environment
    assert fl_mcp.main(["--dialect", "postgres"]) == 0
    assert connected == ["postgresql://reader@warehouse/shop"]
    assert served[1].dialect == "postgres"
    assert fl_mcp.main(["--dsn", "duckdb:///:memory:", "--dialect", "duckdb"]) == 0
    assert connected[-1] == "duckdb:///:memory:"


class _FakeTypes:
    class Tool:
        @staticmethod
        def model_validate(data: dict[str, Any]) -> dict[str, Any]:
            return data

    class TextContent:
        def __init__(self, type: str, text: str) -> None:
            self.type, self.text = type, text

    class ListToolsResult:
        @staticmethod
        def model_validate(data: dict[str, Any]) -> dict[str, Any]:
            return data

    class CallToolResult:
        @staticmethod
        def model_validate(data: dict[str, Any]) -> dict[str, Any]:
            return data


class _FakeServerV1:
    """mcp 1.x shape: decorator registration."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.handlers: dict[str, Any] = {}

    def list_tools(self) -> Any:
        return lambda fn: self.handlers.setdefault("list", fn)

    def call_tool(self) -> Any:
        return lambda fn: self.handlers.setdefault("call", fn)


class _FakeServerV2:
    """mcp 2.x shape: ``on_*`` handlers, no ``list_tools`` attribute."""

    def __init__(self, name: str, *, on_list_tools: Any, on_call_tool: Any) -> None:
        self.name = name
        self.handlers = {"list": on_list_tools, "call": on_call_tool}


def _fake_mcp(monkeypatch: pytest.MonkeyPatch, server: type) -> None:
    lowlevel = types.SimpleNamespace(Server=server)
    modules = {"mcp.server.lowlevel": lowlevel, "mcp.types": _FakeTypes}
    monkeypatch.setattr(fl_mcp, "_mcp", lambda name: modules[name])


def test_v1_server_raises_on_a_failed_call_so_the_sdk_sets_is_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _fake_mcp(monkeypatch, _FakeServerV1)
    server = fl_mcp.build_server(Checker(dialect="duckdb", catalog=NullCatalog()))
    listed = asyncio.run(server.handlers["list"]())
    assert [t["name"] for t in listed] == ["factlang_check", "factlang_lint", "factlang_explain"]
    ok = asyncio.run(server.handlers["call"]("factlang_lint", {"claims": "UNIQUE orders.id"}))
    assert json.loads(ok[0].text)["ok"] is True
    with pytest.raises(fl_mcp.ToolCallFailed) as exc:
        asyncio.run(server.handlers["call"]("factlang_lint", None))
    assert json.loads(str(exc.value))["error"]["code"] == "invalid_arguments"


def test_v2_server_returns_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_mcp(monkeypatch, _FakeServerV2)
    server = fl_mcp.build_server(Checker(dialect="duckdb", catalog=NullCatalog()))
    params = types.SimpleNamespace(name="nope", arguments={})
    result = asyncio.run(server.handlers["call"](None, params))
    assert result["isError"] is True
    assert json.loads(result["content"][0].text)["error"]["code"] == "unknown_tool"
    listed = asyncio.run(server.handlers["list"](None, None))
    assert len(listed["tools"]) == 3


def test_main_needs_a_dsn_or_a_dialect(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("FACTLANG_DSN", raising=False)
    with pytest.raises(SystemExit) as exc:
        fl_mcp.main([])
    assert exc.value.code == 2


def test_main_without_the_extra_exits_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _without_mcp(monkeypatch)
    assert fl_mcp.main(["--dialect", "duckdb"]) == 2
    assert "factlang[mcp]" in capsys.readouterr().err


def test_main_builds_the_checker_and_serves(monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[Checker] = []
    monkeypatch.setattr(fl_mcp, "serve", lambda checker, **kw: served.append(checker))
    assert fl_mcp.main(["--dialect", "postgres"]) == 0
    assert served[0].dialect == "postgres" and served[0].connector is None

    connected: list[str] = []
    duckdb = pytest.importorskip("duckdb")
    conn = DBAPIConnector(duckdb.connect(), "duckdb")

    def fake_connect(dsn: str) -> DBAPIConnector:
        connected.append(dsn)
        return conn

    monkeypatch.setattr(fl_mcp, "connect", fake_connect)
    assert fl_mcp.main(["--dsn", "duckdb:///:memory:", "--max-bytes", "1000"]) == 0
    assert connected == ["duckdb:///:memory:"]
    assert served[1].connector is conn and served[1].dialect == "duckdb"
    assert served[1].max_bytes == 1000


def test_main_reports_a_bad_dsn(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def broken(dsn: str) -> Any:
        raise ValueError("cannot parse URL")

    monkeypatch.setattr(fl_mcp, "connect", broken)
    monkeypatch.setattr(fl_mcp, "serve", lambda checker, **kw: None)
    assert fl_mcp.main(["--dsn", "nonsense"]) == 2
    assert "cannot parse URL" in capsys.readouterr().err


# -- live server: needs ``mcp`` --------------------------------------------------------------


@pytest.fixture()
def duck_checker() -> Iterator[Checker]:
    duckdb = pytest.importorskip("duckdb")
    db = duckdb.connect()
    db.execute(
        "CREATE TABLE orders(id INT, status VARCHAR);"
        "INSERT INTO orders VALUES (1, 'paid'), (2, 'open');"
    )
    yield Checker(DBAPIConnector(db, "duckdb"))
    db.close()


def _connected(server: Any) -> Any:
    """An in-process client session for ``server`` (mcp 2.x ``Client``, 1.x memory helper)."""
    mcp = pytest.importorskip("mcp")
    if hasattr(mcp, "Client"):
        return mcp.Client(server)
    memory = pytest.importorskip("mcp.shared.memory")
    return memory.create_connected_server_and_client_session(server)


def _schema(tool: Any) -> Any:
    return getattr(tool, "input_schema", None) or tool.inputSchema


def _is_error(result: Any) -> Any:
    return getattr(result, "is_error", None) or getattr(result, "isError", None)


def test_server_lists_and_calls_the_tools(duck_checker: Checker) -> None:
    pytest.importorskip("mcp")
    server = fl_mcp.build_server(duck_checker)

    async def run() -> tuple[Any, Any, Any, Any]:
        async with _connected(server) as client:
            listed = await client.list_tools()
            checked = await client.call_tool(
                "factlang_check", {"claims": "ROWS orders VALUE == 2\nUNIQUE orders.id"}
            )
            explained = await client.call_tool(
                "factlang_explain", {"claims": "ROWS orders VALUE == 2", "dialect": "duckdb"}
            )
            failed = await client.call_tool("factlang_lint", {"claims": 3})
            return listed, checked, explained, failed

    listed, checked, explained, failed = asyncio.run(run())
    assert [t.name for t in listed.tools] == ["factlang_check", "factlang_lint", "factlang_explain"]
    assert _schema(listed.tools[0])["additionalProperties"] is False
    report = json.loads(checked.content[0].text)
    assert [o["verdict"] for o in report["outcomes"]] == ["pass", "pass"]
    assert not _is_error(checked)
    assert json.loads(explained.content[0].text)["claims"][0]["kind"] == "probe"
    # a schema-violating call is refused (by the SDK's validation or by handle_tool_call)
    assert _is_error(failed) or "invalid_arguments" in failed.content[0].text


def test_max_claims_option_and_call_tool_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    parser = fl_mcp.configure_parser(argparse.ArgumentParser())
    assert parser.parse_args([]).max_claims == 200
    assert parser.parse_args(["--max-claims", "7"]).max_claims == 7
    for bad in ("0", "-3", "x"):
        with pytest.raises(SystemExit):
            parser.parse_args(["--max-claims", bad])
    checker = Checker(dialect="duckdb")
    claims = "\n".join("UNIQUE orders.id" for _ in range(3))
    args = {"claims": claims, "dialect": "duckdb"}
    err = json.loads(fl_mcp.call_tool(checker, "factlang_explain", args, max_claims=2))
    assert err["error"]["code"] == "too_many_claims"
    assert "error" not in json.loads(fl_mcp.call_tool(checker, "factlang_explain", args))
    served: list[dict[str, Any]] = []
    monkeypatch.setattr(fl_mcp, "serve", lambda checker, **kw: served.append(kw))
    assert fl_mcp.main(["--dialect", "duckdb", "--max-claims", "9"]) == 0
    assert served[0]["max_claims"] == 9


@pytest.mark.parametrize("server_cls", [_FakeServerV1, _FakeServerV2])
def test_build_server_threads_max_claims_into_dispatch(
    monkeypatch: pytest.MonkeyPatch, server_cls: type
) -> None:
    _fake_mcp(monkeypatch, server_cls)
    seen: list[Any] = []
    real = fl_mcp._dispatch

    def spy(checker: Checker, name: str, arguments: Any, max_claims: Any = None) -> Any:
        seen.append(max_claims)
        return real(checker, name, arguments, max_claims)

    monkeypatch.setattr(fl_mcp, "_dispatch", spy)
    server = fl_mcp.build_server(Checker(dialect="duckdb"), max_claims=2)
    args = {"claims": "UNIQUE a.b\nUNIQUE a.c\nUNIQUE a.d", "dialect": "duckdb"}
    if server_cls is _FakeServerV1:
        with pytest.raises(fl_mcp.ToolCallFailed) as exc:
            asyncio.run(server.handlers["call"]("factlang_explain", args))
        text = str(exc.value)
    else:
        params = types.SimpleNamespace(name="factlang_explain", arguments=args)
        result = asyncio.run(server.handlers["call"](None, params))
        assert result["isError"] is True
        text = result["content"][0].text
    assert seen == [2]
    assert json.loads(text)["error"]["code"] == "too_many_claims"
