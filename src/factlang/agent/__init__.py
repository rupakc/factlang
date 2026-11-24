"""The agent surface (spec 10; layer 17): LLM tool definitions, the prompt-ready verb
reference and a JSON tool-call dispatcher. The optional MCP server lives in
:mod:`factlang.agent.mcp` (extra ``factlang[mcp]``) and is not imported here."""

from __future__ import annotations

from factlang.agent.reference import verb_reference
from factlang.agent.tools import ERROR_CODES, TOOL_NAMES, handle_tool_call, tool_definitions

__all__ = ["ERROR_CODES", "TOOL_NAMES", "handle_tool_call", "tool_definitions", "verb_reference"]
