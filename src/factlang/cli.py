"""The ``factlang`` command (spec 10; layer 17), stdlib ``argparse`` only.

Subcommands: ``lint FILE…``, ``fmt [--check] FILE…``, ``explain FILE --dialect D``,
``check FILE (--dsn URL | --connector module:attr [--dialect D])`` and ``verbs [--json]``;
``--format text|json|markdown`` before or after the subcommand.

Exit codes: 0 ok · 1 a claim FAILED (``fmt --check``: a file would be reformatted) ·
2 invalid input (bad usage, unreadable file, a claim or directive with front-end errors,
a connector that cannot be loaded) · 3 errors or undecidable (an ``ERROR`` or ``NO_DATA``
outcome, a claim that cannot be rendered). When several apply: 2, then 1, then 3.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TextIO

from factlang.__about__ import __version__
from factlang.api import format as format_text
from factlang.api import lint
from factlang.connectors import DBAPIConnector, connect
from factlang.connectors.base import AsyncConnector, Connector
from factlang.diagnostics import DiagnosticBag
from factlang.engine import Checker, Report, string_quoter
from factlang.judge import Outcome, Verdict, json_safe
from factlang.packs.standard import standard_pack
from factlang.sql import RenderedProbe
from factlang.syntax import ast
from factlang.syntax.lexer import tokenize
from factlang.syntax.parser import parse
from factlang.syntax.tokens import TokenKind

EXIT_OK, EXIT_FAILED, EXIT_INVALID, EXIT_ERROR = 0, 1, 2, 3
FORMATS = ("text", "json", "markdown")


class UsageError(Exception):
    """Invalid input that is not a claim's outcome: reported on stderr, exit 2."""


# -- helpers ---------------------------------------------------------------------------------


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise UsageError(f"cannot read {path}: {exc}") from exc


def _cell(text: str) -> str:
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")


def _dump(data: object, out: TextIO) -> None:
    out.write(json.dumps(json_safe(data), indent=2) + "\n")


def _verdict_exit(verdicts: Sequence[Verdict], front_end_errors: bool = False) -> int:
    if front_end_errors or Verdict.INVALID in verdicts:
        return EXIT_INVALID
    if Verdict.FAIL in verdicts:
        return EXIT_FAILED
    if Verdict.ERROR in verdicts or Verdict.NO_DATA in verdicts:
        return EXIT_ERROR
    return EXIT_OK


# -- lint ------------------------------------------------------------------------------------


def _location(d: dict[str, Any]) -> tuple[int, int]:
    span = d.get("span")
    return (span["line"], span["col"]) if isinstance(span, dict) else (0, 0)


def cmd_lint(args: argparse.Namespace, out: TextIO) -> int:
    results: list[tuple[str, DiagnosticBag]] = [(p, lint(_read(p))) for p in args.files]
    bad = any(bag.has_errors for _, bag in results)
    if args.format == "json":
        files = [{"file": p, "diagnostics": bag.to_list()} for p, bag in results]
        _dump({"ok": not bad, "files": files}, out)
    elif args.format == "markdown":
        out.write("| file | line | code | severity | message |\n|---|---|---|---|---|\n")
        for p, bag in results:
            for d in bag:
                line = d.span.line if d.span is not None else ""
                out.write(
                    f"| {_cell(p)} | {line} | {d.code} | {d.severity.value} "
                    f"| {_cell(d.message)} |\n"
                )
    else:
        total = 0
        for p, bag in results:
            for d in bag:
                total += 1
                where = f"{d.span.line}:{d.span.col}" if d.span is not None else "-"
                body = d.render(bag.source or "")
                out.write(f"{p}:{where}: {body}\n\n")
        if total == 0:
            out.write(f"no problems in {len(results)} file(s)\n")
    return EXIT_INVALID if bad else EXIT_OK


# -- fmt -------------------------------------------------------------------------------------


def _comment_count(text: str) -> int:
    return sum(1 for t in tokenize(text, DiagnosticBag()) if t.kind is TokenKind.COMMENT)


def _formatted(path: str) -> tuple[str, str]:
    text = _read(path)
    try:
        canonical = format_text(text)
    except ValueError as exc:
        raise UsageError(f"{path}: cannot format a file with syntax errors:\n{exc}") from exc
    lost = _comment_count(text) - _comment_count(canonical)
    if lost > 0:
        raise UsageError(
            f"{path}: formatting would drop {lost} comment line(s); FactLang keeps only a "
            "comment at the end of a claim or directive line. Move them there first."
        )
    return text, canonical


def cmd_fmt(args: argparse.Namespace, out: TextIO) -> int:
    pairs = [(p, *_formatted(p)) for p in args.files]  # validate every file before writing
    changed = [p for p, text, canonical in pairs if text != canonical]
    if not args.check:
        for p, text, canonical in pairs:
            if text != canonical:
                Path(p).write_text(canonical, encoding="utf-8")
    if args.format == "json":
        _dump({"files": [{"file": p, "changed": p in changed} for p, _, _ in pairs]}, out)
    else:
        verb = "would reformat" if args.check else "reformatted"
        bullet = "- " if args.format == "markdown" else ""
        for p in changed:
            out.write(f"{bullet}{verb} {p}\n")
        out.write(f"{bullet}{len(pairs) - len(changed)} file(s) unchanged\n")
    return EXIT_FAILED if args.check and changed else EXIT_OK


# -- explain ---------------------------------------------------------------------------------


def _claim_texts(text: str, items: Sequence[RenderedProbe | Outcome]) -> list[str]:
    """The source text of each compiled item. A probe always comes from a parsed claim; an
    outcome does too unless it is a line that did not parse (its own ``claim_text``)."""
    parsed = [i for i in parse(text).document.items if isinstance(i, ast.Claim)]
    pos = 0
    texts: list[str] = []
    for item in items:
        nxt = parsed[pos] if pos < len(parsed) else None
        if isinstance(item, RenderedProbe):
            texts.append(nxt.line_text if nxt is not None else "")
            pos += 1
        elif nxt is not None and item.claim_text == nxt.line_text:
            texts.append(item.claim_text)
            pos += 1
        else:
            texts.append(item.claim_text)
    return texts


def cmd_explain(args: argparse.Namespace, out: TextIO) -> int:
    text = _read(args.file)
    items = Checker(dialect=args.dialect).compile(text)
    claims = _claim_texts(text, items)
    quote = string_quoter(args.dialect)
    records: list[dict[str, object]] = []
    for claim, item in zip(claims, items, strict=True):
        if isinstance(item, RenderedProbe):
            records.append(
                {
                    "claim": claim,
                    "sql": item.inline(quote),
                    "sql_template": item.sql_template,
                    "params": [
                        {"name": p.name, "value": p.value, "family": p.family} for p in item.params
                    ],
                    "dialect": item.dialect,
                    "tier": item.tier,
                }
            )
        else:
            records.append(
                {
                    "claim": claim,
                    "verdict": item.verdict.value,
                    "explanation": item.explanation,
                    "diagnostics": list(item.diagnostics),
                }
            )
    if args.format == "json":
        _dump(records, out)
    else:
        fence = args.format == "markdown"
        for r in records:
            if "sql" in r:
                if fence:
                    out.write(f"**{_cell(str(r['claim']))}**\n\n```sql\n{r['sql']};\n```\n\n")
                else:
                    out.write(f"-- {r['claim']}\n{r['sql']};\n\n")
            else:
                head = f"{r['verdict']}: {r['explanation']}"
                if fence:
                    out.write(f"**{_cell(str(r['claim']))}**\n\n> {_cell(head)}\n\n")
                else:
                    out.write(f"-- {r['claim']}\n-- {head}\n\n")
    verdicts = [i.verdict for i in items if isinstance(i, Outcome)]
    return _verdict_exit(verdicts)


# -- check -----------------------------------------------------------------------------------


def _is_connector(obj: object) -> bool:
    return isinstance(obj, (Connector, AsyncConnector))


def load_connector(target: str, dialect: str | None) -> Connector | AsyncConnector:
    """Resolve ``module:attr``: a connector; a zero-argument factory returning one (or a
    DB-API connection); or a DB-API connection, wrapped in ``DBAPIConnector`` (needs
    ``--dialect``)."""
    module_name, sep, attr = target.partition(":")
    if not sep or not module_name or not attr:
        raise UsageError(f"--connector must be module:attr, got {target!r}")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise UsageError(f"cannot import {module_name!r}: {exc}") from exc
    obj: object = module
    for part in attr.split("."):
        try:
            obj = getattr(obj, part)
        except AttributeError:
            raise UsageError(f"{module_name!r} has no attribute {attr!r}") from None
    if not _is_connector(obj) and callable(obj) and not hasattr(obj, "cursor"):
        try:
            obj = obj()
        except Exception as exc:
            raise UsageError(f"{target} raised {type(exc).__name__}: {exc}") from exc
    if _is_connector(obj):
        return obj  # type: ignore[return-value]
    if hasattr(obj, "cursor"):
        if dialect is None:
            raise UsageError(f"{target} is a DB-API connection: pass --dialect to name its SQL")
        return DBAPIConnector(obj, dialect)
    raise UsageError(f"{target} is not a connector, a factory or a DB-API connection")


def _render_report(report: Report, fmt: str, out: TextIO) -> None:
    if fmt == "json":
        out.write(report.to_json() + "\n")
        return
    if fmt == "markdown":
        out.write(report.to_markdown())
        return
    for o in report.outcomes:
        out.write(f"{o.verdict.name:<11} {o.claim_text.strip()}\n")
        if o.explanation:
            out.write(f"            {o.explanation}\n")
    for d in report.diagnostics:
        if d.severity.value == "error":
            out.write(f"{d.severity.value}[{d.code}]: {d.message}\n")
    counts = ", ".join(f"{n} {v.name}" for v, n in report.summary().items() if n)
    out.write(f"{'ok' if report.ok else 'not ok'}: {counts or 'no claims'}\n")


def cmd_check(args: argparse.Namespace, out: TextIO) -> int:
    text = _read(args.file)
    connector: Connector | AsyncConnector
    if args.dsn is not None:
        try:
            connector = connect(args.dsn)
        except Exception as exc:
            raise UsageError(f"cannot connect to --dsn: {type(exc).__name__}: {exc}") from exc
    else:
        connector = load_connector(args.connector, args.dialect)
    try:
        checker = Checker(connector, dialect=args.dialect)
    except ValueError as exc:
        raise UsageError(str(exc)) from exc
    report = checker.check(text)
    _render_report(report, args.format, out)
    front_end = any(d.severity.value == "error" and d.code < "FL400" for d in report.diagnostics)
    return _verdict_exit([o.verdict for o in report.outcomes], front_end)


# -- verbs -----------------------------------------------------------------------------------


def _verb_records() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for c in standard_pack().contracts():
        s = c.spec
        records.append(
            {
                "name": s.name,
                "summary": s.summary,
                "operands": [{"name": o.name, "kind": o.kind.value} for o in s.operands],
                "attributes": [
                    {
                        "name": a.name,
                        "type": a.type,
                        "required": a.required,
                        "ops": list(a.ops),
                        "doc": a.doc,
                    }
                    for a in s.attrs
                ],
                "filterable": s.filterable,
                "examples": [e.source for e in c.examples if e.valid],
            }
        )
    return records


def cmd_verbs(args: argparse.Namespace, out: TextIO) -> int:
    records = _verb_records()
    fmt = "json" if args.json else args.format
    if fmt == "json":
        _dump(records, out)
    elif fmt == "markdown":
        out.write("| verb | operands | summary |\n|---|---|---|\n")
        for r in records:
            ops = ", ".join(str(o["name"]) for o in r["operands"])  # type: ignore[attr-defined]
            out.write(f"| {r['name']} | {ops} | {_cell(str(r['summary']))} |\n")
    else:
        width = max(len(str(r["name"])) for r in records)
        for r in records:
            out.write(f"{r['name']!s:<{width}}  {r['summary']}\n")
    return EXIT_OK


# -- parser ----------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="factlang",
        description="Check factual claims against a data warehouse.",
        epilog="exit codes: 0 ok, 1 a claim failed, 2 invalid input, 3 errors/undecidable",
    )
    parser.add_argument("--version", action="version", version=f"factlang {__version__}")
    parser.add_argument("--format", choices=FORMATS, default="text", help="output format")
    # the same option after the subcommand; SUPPRESS keeps the top-level value otherwise
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--format", choices=FORMATS, default=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    p = sub.add_parser("lint", parents=[common], help="report diagnostics; never executes")
    p.add_argument("files", nargs="+", metavar="FILE")
    p.set_defaults(func=cmd_lint)

    p = sub.add_parser("fmt", parents=[common], help="rewrite files in canonical form")
    p.add_argument("--check", action="store_true", help="only report; exit 1 if any differ")
    p.add_argument("files", nargs="+", metavar="FILE")
    p.set_defaults(func=cmd_fmt)

    p = sub.add_parser("explain", parents=[common], help="print each claim's SQL")
    p.add_argument("file", metavar="FILE")
    p.add_argument("--dialect", required=True, help="SQL dialect, e.g. postgres")
    p.set_defaults(func=cmd_explain)

    p = sub.add_parser("check", parents=[common], help="check claims on a warehouse")
    p.add_argument("file", metavar="FILE")
    source = p.add_mutually_exclusive_group(required=True)
    source.add_argument("--dsn", help="SQLAlchemy URL (needs factlang[sqlalchemy])")
    source.add_argument(
        "--connector",
        metavar="MODULE:ATTR",
        help="a connector, a factory returning one, or a DB-API connection (with --dialect)",
    )
    p.add_argument("--dialect", help="SQL dialect (default: the connector's)")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("verbs", parents=[common], help="list the standard verbs")
    p.add_argument("--json", action="store_true", help="same as --format json")
    p.set_defaults(func=cmd_verbs)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Callable[[argparse.Namespace, TextIO], int] | None = getattr(args, "func", None)
    if func is None:
        parser.print_help(sys.stderr)
        return EXIT_INVALID
    try:
        return func(args, sys.stdout)
    except UsageError as exc:
        sys.stderr.write(f"factlang: {exc}\n")
        return EXIT_INVALID


__all__ = ["EXIT_ERROR", "EXIT_FAILED", "EXIT_INVALID", "EXIT_OK", "build_parser", "main"]
