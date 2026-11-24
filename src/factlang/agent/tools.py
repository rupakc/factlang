"""LLM tool definitions and their dispatcher (spec 10, agent surface; layer 17).

``tool_definitions(pack)`` returns three function-calling tool specs, each
``{"name", "description", "input_schema"}`` with a JSON Schema (draft 2020-12) object that
forbids extra properties, usable with any function-calling LLM API:

- ``factlang_check(claims)`` checks claims against the warehouse; its description embeds the
  compact verb reference of ``pack``.
- ``factlang_lint(claims)`` runs the front end only (parse + analyse) and never executes SQL.
- ``factlang_explain(claims, dialect)`` renders each claim's probe without executing it.

``factlang_explain``'s ``dialect`` is an ``enum`` of the native dialects; when sqlglot is
installed any sqlglot dialect renders too, so the schema is then a free string with the
native dialects as ``examples``.

``handle_tool_call(name, arguments, checker)`` never raises: a bad call returns
``{"error": {"code", "message", "tool"}}`` with one of the stable codes in
:data:`ERROR_CODES`; ``factlang_check`` returns :meth:`Report.to_dict`, whose first key is
``verified``.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Mapping
from typing import Any

from factlang.agent.reference import verb_reference
from factlang.contract import ContractPack
from factlang.diagnostics import DiagnosticBag
from factlang.engine import Checker, string_quoter
from factlang.judge import Outcome, json_safe
from factlang.semantics.analyzer import analyze
from factlang.sql import RenderedProbe, RenderError, native_dialects, renderer_for
from factlang.syntax import parse

JSON_SCHEMA_DIALECT = "https://json-schema.org/draft/2020-12/schema"
TOOL_NAMES: tuple[str, ...] = ("factlang_check", "factlang_lint", "factlang_explain")
ERROR_CODES: tuple[str, ...] = (
    "unknown_tool",  # no tool of that name
    "invalid_arguments",  # arguments are not an object matching the input schema
    "unknown_dialect",  # factlang_explain: the dialect cannot be rendered (FL504)
    "no_connector",  # factlang_check on a Checker without a connector
    "internal_error",  # anything unexpected (a bug); the message names the exception
)

_CLAIMS = {
    "type": "string",
    "description": (
        "FactLang claims, one per line (a `USE schema` directive may precede them). "
        "Each claim is a VERB, its operands and attributes, optionally `WHERE <condition>`."
    ),
}

_CHECK = (
    "Check factual claims against the data warehouse. Each claim is measured exactly with "
    "one read-only query and judged pass, fail, no_data, error, unprobeable or invalid; the "
    "result lists one outcome per claim, in order, with the measured facts and the SQL "
    "evidence. Read `verified` first: it is true only when every measurable claim passed "
    "(`ok` also tolerates no_data and an empty input). Write claims in FactLang using the "
    "verbs below; run factlang_lint first when unsure about syntax."
)
_LINT = (
    "Validate FactLang claims without touching the warehouse (syntax, verbs, attributes, "
    "value types). Returns diagnostics with codes, positions, hints and did-you-mean "
    "suggestions. Use it to fix claims before calling factlang_check."
)
_EXPLAIN = (
    "Show the SQL each FactLang claim would run, for the given SQL dialect, without "
    "executing anything. Returns one entry per claim, in order: its probe (display SQL with "
    "literals inlined, the parameterised template and typed parameters) or, when it has no "
    "probe, its outcome (e.g. invalid with diagnostics)."
)


def sqlglot_available() -> bool:
    """Whether sqlglot is importable (then any sqlglot dialect renders through the bridge)."""
    return importlib.util.find_spec("sqlglot") is not None


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "$schema": JSON_SCHEMA_DIALECT,
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _dialect_schema() -> dict[str, Any]:
    natives = native_dialects()
    if sqlglot_available():
        return {
            "type": "string",
            "description": (
                "SQL dialect to render for: a native dialect (see examples) or any other "
                "dialect name sqlglot knows."
            ),
            "examples": natives,
        }
    return {"type": "string", "description": "SQL dialect to render for.", "enum": natives}


def tool_definitions(pack: ContractPack | None = None) -> list[dict[str, Any]]:
    """The three tool specs for ``pack`` (default: the standard pack)."""
    reference = verb_reference(pack, style="compact")
    return [
        {
            "name": "factlang_check",
            "description": f"{_CHECK}\n\n{reference}",
            "input_schema": _schema({"claims": dict(_CLAIMS)}, ["claims"]),
        },
        {
            "name": "factlang_lint",
            "description": _LINT,
            "input_schema": _schema({"claims": dict(_CLAIMS)}, ["claims"]),
        },
        {
            "name": "factlang_explain",
            "description": _EXPLAIN,
            "input_schema": _schema(
                {"claims": dict(_CLAIMS), "dialect": _dialect_schema()}, ["claims", "dialect"]
            ),
        },
    ]


# -- dispatch --------------------------------------------------------------------------------

_PARAMS: dict[str, tuple[str, ...]] = {
    "factlang_check": ("claims",),
    "factlang_lint": ("claims",),
    "factlang_explain": ("claims", "dialect"),
}


class _ToolError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _error(name: object, code: str, message: str) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "tool": name}}


def _arguments(name: str, arguments: object) -> dict[str, str]:
    if not isinstance(arguments, Mapping):
        raise _ToolError(
            "invalid_arguments", f"arguments must be a JSON object, got {type(arguments).__name__}"
        )
    params = _PARAMS[name]
    extra = sorted(str(k) for k in arguments if k not in params)
    if extra:
        raise _ToolError("invalid_arguments", f"unexpected argument(s): {', '.join(extra)}")
    out: dict[str, str] = {}
    for p in params:
        if p not in arguments:
            raise _ToolError("invalid_arguments", f"missing required argument {p!r}")
        value = arguments[p]
        if not isinstance(value, str):
            raise _ToolError(
                "invalid_arguments", f"argument {p!r} must be a string, got {type(value).__name__}"
            )
        out[p] = value
    return out


def _check(checker: Checker, claims: str) -> dict[str, object]:
    if checker.connector is None:
        raise _ToolError(
            "no_connector",
            "this server has no warehouse connection; use factlang_lint or factlang_explain",
        )
    return checker.check(claims).to_dict()


def _lint(checker: Checker, claims: str) -> dict[str, object]:
    """Front end only: parse + analyse with the checker's pack, never a warehouse catalog."""
    parsed = parse(claims)
    bag = DiagnosticBag()
    bag.extend(parsed.diagnostics)
    checked = analyze(parsed.document, checker.pack.verb_registry(), catalog=None, bag=bag)
    return {
        "ok": not bag.has_errors,
        "claims": len(checked),
        "diagnostics": json_safe(bag.to_list()),
        "rendered": bag.render(claims),
    }


def _probe_entry(probe: RenderedProbe) -> dict[str, object]:
    return {
        "kind": "probe",
        "dialect": probe.dialect,
        "tier": probe.tier,
        "sql": probe.inline(string_quoter(probe.dialect)),
        "sql_template": probe.sql_template,
        "params": json_safe(
            [{"name": p.name, "value": p.value, "family": p.family} for p in probe.params]
        ),
    }


def _explain(checker: Checker, claims: str, dialect: str) -> dict[str, object]:
    try:
        renderer_for(dialect, prefer=checker.prefer)
    except RenderError as exc:
        raise _ToolError("unknown_dialect", str(exc)) from None
    target = checker
    if dialect != checker.dialect:
        target = Checker(
            dialect=dialect,
            pack=checker.pack,
            catalog=checker.offline_catalog(),
            prefer=checker.prefer,
        )
    entries: list[dict[str, object]] = []
    for item in target.compile(claims):
        if isinstance(item, Outcome):
            entries.append({"kind": "outcome", "outcome": item.to_dict()})
        else:
            entries.append(_probe_entry(item))
    return {"dialect": dialect, "claims": entries}


def handle_tool_call(name: str, arguments: dict[str, Any], checker: Checker) -> dict[str, object]:
    """Run tool ``name`` with ``arguments`` on ``checker``; a JSON-safe dict, never raises."""
    if not isinstance(name, str) or name not in _PARAMS:
        known = ", ".join(TOOL_NAMES)
        return _error(
            name if isinstance(name, str) else None,
            "unknown_tool",
            f"unknown tool {name!r}; tools: {known}",
        )
    try:
        args = _arguments(name, arguments)
        if name == "factlang_check":
            return _check(checker, args["claims"])
        if name == "factlang_lint":
            return _lint(checker, args["claims"])
        return _explain(checker, args["claims"], args["dialect"])
    except _ToolError as exc:
        return _error(name, exc.code, exc.message)
    except Exception as exc:
        return _error(name, "internal_error", f"{type(exc).__name__}: {exc}")


__all__ = [
    "ERROR_CODES",
    "JSON_SCHEMA_DIALECT",
    "TOOL_NAMES",
    "handle_tool_call",
    "sqlglot_available",
    "tool_definitions",
]
