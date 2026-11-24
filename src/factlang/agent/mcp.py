"""An MCP server exposing the FactLang tools over stdio (spec 10; layer 17, non-core).

Needs the extra ``factlang[mcp]`` (the official ``mcp`` package), which is imported lazily
inside :func:`build_server` and :func:`serve` only; importing this module never imports it.
The registration data (:func:`tool_registrations`) and the dispatcher (:func:`call_tool`) are
plain functions, so they are testable without the package.

Command line: ``factlang mcp --dsn URL`` (or ``python -m factlang.agent.mcp --dsn URL``);
``--dialect D`` alone serves without a warehouse (``factlang_check`` then returns a
``no_connector`` error, ``factlang_lint`` and ``factlang_explain`` work).
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from collections.abc import Sequence
from types import ModuleType
from typing import Any

from factlang.agent.tools import handle_tool_call, tool_definitions
from factlang.connectors import connect
from factlang.contract import ContractPack
from factlang.engine import Checker

INSTALL_HINT = "the MCP server needs the mcp package: pip install 'factlang[mcp]'"
SERVER_NAME = "factlang"


def _mcp(module: str) -> ModuleType:
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        if exc.name is not None and (exc.name == "mcp" or exc.name.startswith("mcp.")):
            raise ImportError(INSTALL_HINT) from exc
        raise


def tool_registrations(pack: ContractPack | None = None) -> list[dict[str, Any]]:
    """The tools as MCP registers them: ``{"name", "description", "inputSchema"}``."""
    return [
        {"name": t["name"], "description": t["description"], "inputSchema": t["input_schema"]}
        for t in tool_definitions(pack)
    ]


def _dispatch(checker: Checker, name: str, arguments: Any) -> tuple[str, bool]:
    result = handle_tool_call(name, arguments, checker)
    return json.dumps(result, indent=2), "error" in result


def call_tool(checker: Checker, name: str, arguments: Any) -> str:
    """One tool call as the JSON text an MCP ``TextContent`` carries; never raises."""
    return _dispatch(checker, name, arguments)[0]


def build_server(checker: Checker, *, name: str = SERVER_NAME) -> Any:
    """A low-level ``mcp`` server with the three tools registered (needs ``factlang[mcp]``).

    Works with both ``mcp`` 1.x (decorator registration) and 2.x (``on_*`` handlers); a
    failed call is a normal result carrying the ``{"error": ...}`` JSON (``isError`` set
    where the version supports it).
    """
    lowlevel = _mcp("mcp.server.lowlevel")
    types = _mcp("mcp.types")
    tools = [types.Tool.model_validate(r) for r in tool_registrations(checker.pack)]

    async def run_tool(tool: str, arguments: Any) -> tuple[Any, bool]:
        args = arguments if arguments is not None else {}
        # checks block on the warehouse: keep the event loop free
        text, failed = await asyncio.to_thread(_dispatch, checker, tool, args)
        return types.TextContent(type="text", text=text), failed

    if hasattr(lowlevel.Server, "list_tools"):  # mcp 1.x
        server = lowlevel.Server(name)

        async def list_tools_v1() -> list[Any]:
            return tools

        async def call_tool_v1(tool: str, arguments: dict[str, Any] | None) -> list[Any]:
            content, _ = await run_tool(tool, arguments)
            return [content]

        server.list_tools()(list_tools_v1)
        server.call_tool()(call_tool_v1)
        return server

    async def list_tools(ctx: Any, params: Any) -> Any:  # mcp 2.x
        return types.ListToolsResult.model_validate({"tools": tools})

    async def call(ctx: Any, params: Any) -> Any:
        content, failed = await run_tool(params.name, params.arguments)
        return types.CallToolResult.model_validate({"content": [content], "isError": failed})

    return lowlevel.Server(name, on_list_tools=list_tools, on_call_tool=call)


def serve(checker: Checker, *, name: str = SERVER_NAME) -> None:
    """Serve the tools over stdio until the client disconnects (needs ``factlang[mcp]``)."""
    server = build_server(checker, name=name)
    stdio = _mcp("mcp.server.stdio")

    async def run() -> None:
        async with stdio.stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())

    asyncio.run(run())


# -- command line ----------------------------------------------------------------------------


def configure_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
    """Add the ``mcp`` options to ``parser`` (used by ``factlang mcp`` and :func:`main`)."""
    parser.add_argument("--dsn", help="SQLAlchemy URL of the warehouse to check against")
    parser.add_argument("--dialect", help="SQL dialect (default: the connection's)")
    parser.add_argument(
        "--timeout", type=float, default=60.0, help="per-probe timeout in seconds (default 60)"
    )
    parser.add_argument(
        "--max-bytes", type=int, default=None, help="refuse probes priced above this (dry run)"
    )
    parser.add_argument("--name", default=SERVER_NAME, help="MCP server name")
    return parser


def run(args: argparse.Namespace) -> int:
    """Build the Checker from parsed options and serve; 2 on a configuration error."""
    if args.dsn is None and args.dialect is None:
        print("factlang mcp: give --dsn URL or --dialect D", file=sys.stderr)
        return 2
    try:
        connector = connect(args.dsn) if args.dsn is not None else None
        checker = Checker(
            connector, dialect=args.dialect, timeout_s=args.timeout, max_bytes=args.max_bytes
        )
    except Exception as exc:  # a bad URL, a missing driver: configuration, not a crash
        print(f"factlang mcp: cannot connect: {exc}", file=sys.stderr)
        return 2
    try:
        serve(checker, name=args.name)
    except ImportError as exc:
        print(f"factlang mcp: {exc}", file=sys.stderr)
        return 2
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = configure_parser(
        argparse.ArgumentParser(
            prog="factlang mcp", description="Serve the FactLang tools over MCP (stdio)."
        )
    )
    args = parser.parse_args(argv)
    if args.dsn is None and args.dialect is None:
        parser.error("give --dsn URL or --dialect D")
    return run(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())


__all__ = [
    "INSTALL_HINT",
    "build_server",
    "call_tool",
    "configure_parser",
    "main",
    "run",
    "serve",
    "tool_registrations",
]
