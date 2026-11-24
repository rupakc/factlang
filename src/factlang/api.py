"""The public facade (spec 10, 11; layer 16): one-call helpers over the layers below.

``import factlang as fl`` re-exports exactly :data:`__all__` from here. Everything named in
``__all__`` is public API under SemVer (spec 11, Amendment A R10).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, overload

from factlang import connectors, ir, sql
from factlang.__about__ import LANGUAGE_VERSION, __version__
from factlang.connectors import (
    BigQueryConnector,
    CallableConnector,
    DBAPIConnector,
    SQLAlchemyConnector,
    WarehouseCatalog,
    connect,
    run_metadata,
)
from factlang.connectors.base import AsyncConnector, Connector
from factlang.contract import Contract, ContractPack, Example, check_pack, discover_packs
from factlang.diagnostics import DiagnosticBag
from factlang.engine import Checker, CostEstimate, Measurement, Report
from factlang.ir import Metric, ProbePlan
from factlang.judge import (
    And,
    Compare,
    CompareUsingClaimOp,
    Not,
    Or,
    Outcome,
    Verdict,
    WhenClaimHas,
    Within,
)
from factlang.packs.standard import standard_pack
from factlang.semantics import types
from factlang.semantics.analyzer import analyze, analyze_filter
from factlang.semantics.catalog import CachedCatalog, Catalog, NullCatalog, StaticCatalog
from factlang.semantics.filters import FilterExpr
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef
from factlang.semantics.signature import AttrSpec as Attr
from factlang.semantics.signature import Operand
from factlang.sql import RenderedFragment, RenderedProbe
from factlang.syntax.formatter import format_document
from factlang.syntax.parser import parse, parse_condition


def lint(
    text: str, *, pack: ContractPack | None = None, catalog: Catalog | None = None
) -> DiagnosticBag:
    """Every front-end diagnostic of ``text`` (parse + analysis); never executes anything.

    The bag remembers ``text``, so ``fl.lint(text).render()`` prints caret diagnostics.
    """
    result = parse(text)
    bag = DiagnosticBag(text)
    bag.extend(result.diagnostics)
    registry = (pack if pack is not None else standard_pack()).verb_registry()
    analyze(result.document, registry, catalog=catalog, bag=bag)
    return bag


def format(text: str) -> str:  # shadows the builtin on purpose: spec 10 names it fl.format
    """The canonical form of ``text``. Raises ``ValueError`` (with the rendered diagnostics)
    when ``text`` does not parse: formatting it would silently drop the broken lines."""
    result = parse(text)
    if result.diagnostics.has_errors:
        raise ValueError(result.diagnostics.render(text))
    return format_document(result.document)


def compile(  # shadows the builtin on purpose: spec 10 names it fl.compile
    text: str, *, dialect: str, pack: ContractPack | None = None
) -> list[RenderedProbe]:
    """Each claim's own probe for ``dialect``, in source order, without a warehouse.

    Unprobeable (semantic) claims have no SQL and are skipped. Any invalid claim, or a claim
    that cannot be rendered for ``dialect``, raises ``ValueError`` naming every such claim
    and its diagnostic codes.
    """
    problems: list[str] = []
    probes: list[RenderedProbe] = []
    for item in Checker(dialect=dialect, pack=pack).compile(text):
        if isinstance(item, RenderedProbe):
            probes.append(item)
        elif item.verdict is not Verdict.UNPROBEABLE:
            codes = ", ".join(str(d.get("code")) for d in item.diagnostics)
            problems.append(
                f"{item.claim_text.strip()}: {item.verdict.value}"
                + (f" ({codes})" if codes else "")
                + f" - {item.explanation}"
            )
    if problems:
        raise ValueError("cannot compile:\n" + "\n".join(problems))
    return probes


def check(
    text: str, *, connector: Connector | AsyncConnector, **checker_kwargs: Any
) -> Outcome | Report:
    """Check ``text`` on ``connector``: the ``Outcome`` when it holds exactly one claim, else
    the ``Report``. ``checker_kwargs`` go to :class:`~factlang.engine.Checker`."""
    report = Checker(connector, **checker_kwargs).check(text)
    if len(report.outcomes) == 1:
        return report.outcomes[0]
    return report


@overload
def measure(
    plans: ProbePlan, *, connector: Connector | AsyncConnector, **checker_kwargs: Any
) -> Measurement: ...


@overload
def measure(
    plans: Sequence[ProbePlan], *, connector: Connector | AsyncConnector, **checker_kwargs: Any
) -> list[Measurement]: ...


def measure(
    plans: ProbePlan | Sequence[ProbePlan],
    *,
    connector: Connector | AsyncConnector,
    **checker_kwargs: Any,
) -> Measurement | list[Measurement]:
    """Run IR primitives with no claim (R1, B3): one plan gives one ``Measurement``, a
    sequence of plans a list (fused like ``Checker.measure``). Never raises for a failure."""
    checker = Checker(connector, **checker_kwargs)
    if isinstance(plans, Sequence):
        return checker.measure(list(plans))
    return checker.measure([plans])[0]


def parse_filter(
    text: str, *, catalog: Catalog | None = None, table: TableRef | None = None
) -> tuple[FilterExpr | None, DiagnosticBag]:
    """Parse and analyse a bare spec 4.5 condition (R6), with or without a leading ``WHERE``.

    Parameters are ``p1, p2, ...`` in source order. With a ``catalog`` and a ``table`` the
    columns are checked as in a claim. The filter is ``None`` when there is any error.
    """
    cond, parsed = parse_condition(text)
    bag = DiagnosticBag(text)
    bag.extend(parsed)
    if cond is None:
        return None, bag
    return analyze_filter(cond, catalog=catalog, table=table, bag=bag), bag


__all__ = [
    "LANGUAGE_VERSION",
    "And",
    "Attr",
    "BigQueryConnector",
    "CachedCatalog",
    "CallableConnector",
    "Catalog",
    "Checker",
    "ColumnRef",
    "Compare",
    "CompareUsingClaimOp",
    "Contract",
    "ContractPack",
    "CostEstimate",
    "DBAPIConnector",
    "EdgeRef",
    "Example",
    "Ident",
    "Measurement",
    "Metric",
    "Not",
    "NullCatalog",
    "Operand",
    "Or",
    "Outcome",
    "RenderedFragment",
    "Report",
    "SQLAlchemyConnector",
    "StaticCatalog",
    "TableRef",
    "Verdict",
    "WarehouseCatalog",
    "WhenClaimHas",
    "Within",
    "__version__",
    "check",
    "check_pack",
    "compile",
    "connect",
    "connectors",
    "discover_packs",
    "format",
    "ir",
    "lint",
    "measure",
    "parse",
    "parse_filter",
    "run_metadata",
    "sql",
    "standard_pack",
    "types",
]
