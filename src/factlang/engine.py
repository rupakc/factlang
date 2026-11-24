"""The ``Checker``: parse → analyse → plan → optimise → render → execute → judge
(spec 5, 7, 9.3, 18 R1/R5, 19 B3-B4; layer 15).

Every claim of a document becomes exactly one :class:`~factlang.judge.Outcome`, in source
order: ``INVALID`` (front-end diagnostics attached), ``UNPROBEABLE`` (semantic contracts),
catalog verbs judged through the :class:`~factlang.semantics.catalog.Catalog`, and planned
claims judged from one probe each (de-duplicated by semantic hash and, unless
``fuse=False``, fused per table/filter). Execution failures never raise: ``FL7xx`` (and
``FL4xx``/``FL5xx`` planning and rendering errors) become ``ERROR`` outcomes.

``measure`` runs IR primitives with no claim (R1; sampled plans allowed, B3) and
``estimate`` prices claims or plans through the connector's optional dry run without
executing anything (R5).
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from typing import Any, Literal

from factlang.connectors.base import (
    AsyncConnector,
    Connector,
    ExecutionError,
    aexecute,
    dry_run_bytes,
    map_error,
)
from factlang.connectors.catalog import WarehouseCatalog
from factlang.contract import Contract, ContractPack, plan_for
from factlang.diagnostics import Diagnostic, DiagnosticBag, Severity
from factlang.ir import MetadataQuery, ProbePlan, SampleSpec, canonical_key
from factlang.judge import (
    Evidence,
    Outcome,
    Verdict,
    decide_facts,
    json_safe,
    judge_rows,
    payload_for,
    sample_method,
)
from factlang.optimizer import FusedAggregate, FusedProbe, WorkItem, optimize, split
from factlang.packs.standard import standard_pack
from factlang.semantics.analyzer import analyze
from factlang.semantics.catalog import CachedCatalog, Catalog, NullCatalog
from factlang.semantics.checked import CheckedClaim
from factlang.sql import RenderedProbe, RenderError, renderer_for
from factlang.sql.probe import ansi_quote
from factlang.syntax import ast, parse

Prefer = Literal["native", "sqlglot"]
_CODE = re.compile(r"^(FL\d{3})\b")
_LEAD = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)")
# dialects whose string literals treat a backslash as an escape character
_BACKSLASH_STRINGS = frozenset({"bigquery", "mysql", "databricks", "clickhouse"})
_NOT_OK = frozenset({Verdict.FAIL, Verdict.ERROR, Verdict.INVALID})


def string_quoter(dialect: str) -> Callable[[str], str]:
    """The string-literal quoting used to inline parameters into display SQL (evidence only)."""
    if dialect in _BACKSLASH_STRINGS:

        def quote(text: str) -> str:
            return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"

        return quote
    return ansi_quote


# -- results ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Measurement:
    """A raw measurement (R1): ``payload`` is exactly what a contract's ``derive`` would get.

    ``error`` is ``None`` or the ``Diagnostic`` (``FL7xx``, or ``FL4xx``/``FL5xx`` when the plan
    could not be rendered); ``verdict_hint`` is ``ERROR`` on an error, ``NO_DATA`` when the probe
    returned no rows, else ``None``.
    """

    plan: ProbePlan
    payload: dict[str, object] | None
    verdict_hint: Verdict | None
    evidence: Evidence | None
    error: Diagnostic | None


@dataclass(frozen=True)
class CostEstimate:
    """A dry-run price (R5): the claim's semantic hash or the plan's canonical key, the display
    SQL (``None`` when the plan cannot be rendered), the bytes and how they were obtained."""

    semantic_hash_or_plan_key: str
    sql: str | None
    bytes: int | None
    method: Literal["dry_run", "unknown"]

    @property
    def key(self) -> str:
        """Read-only alias of :attr:`semantic_hash_or_plan_key`."""
        return self.semantic_hash_or_plan_key


@dataclass(frozen=True)
class Report:
    """Ordered outcomes of one document plus every diagnostic raised while checking it."""

    outcomes: tuple[Outcome, ...]
    diagnostics: DiagnosticBag

    @property
    def ok(self) -> bool:
        """No ``FAIL``, ``ERROR`` or ``INVALID`` outcome and no error diagnostic (e.g. a
        broken ``USE`` directive, which is not an outcome of its own)."""
        if self.diagnostics.has_errors:
            return False
        return not any(o.verdict in _NOT_OK for o in self.outcomes)

    def summary(self) -> dict[Verdict, int]:
        counts = dict.fromkeys(Verdict, 0)
        for o in self.outcomes:
            counts[o.verdict] += 1
        return counts

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "summary": {v.value: n for v, n in self.summary().items()},
            "outcomes": [o.to_dict() for o in self.outcomes],
            "diagnostics": json_safe(self.diagnostics.to_list()),
        }

    def to_json(self, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)

    def to_markdown(self) -> str:
        def cell(text: str) -> str:
            return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", " ")

        counts = " · ".join(f"{v.value} {n}" for v, n in self.summary().items() if n)
        lines = [
            "# FactLang report",
            "",
            f"**ok:** {'yes' if self.ok else 'no'}" + (f" · {counts}" if counts else ""),
            "",
            "| # | verdict | claim | explanation |",
            "|---|---|---|---|",
        ]
        for i, o in enumerate(self.outcomes, start=1):
            lines.append(
                f"| {i} | {o.verdict.value} | {cell(o.claim_text)} | {cell(o.explanation)} |"
            )
        return "\n".join(lines) + "\n"


# -- internal plumbing -----------------------------------------------------------------------


@dataclass
class _Slot:
    """One claim (or one unparseable line) of the document, in source order."""

    line: int
    outcome: Outcome | None = None
    claim: CheckedClaim | None = None
    contract: Contract | None = None
    plan: ProbePlan | None = None
    diags: tuple[Diagnostic, ...] = ()


@dataclass
class _Run:
    rows: list[dict[str, Any]] | None
    error: ExecutionError | None
    duration_ms: float | None
    bytes_processed: int | None


@dataclass
class _Result:
    """One work item's share of a probe: its rows, or the error, plus the evidence."""

    rows: list[dict[str, object]] | None
    code: str | None
    message: str | None
    evidence: Evidence | None


def _push(bag: DiagnosticBag, d: Diagnostic) -> None:
    add = {Severity.ERROR: bag.error, Severity.WARNING: bag.warning, Severity.INFO: bag.info}
    add[d.severity](d.code, d.message, d.span, hint=d.hint, suggestion=d.suggestion)


def _continues(raw: str) -> bool:
    return raw.rstrip(" \t").endswith("\\")


def _first_line(src_lines: Sequence[str], line: int) -> int:
    """The first physical line of the claim holding ``line`` (walks back over ``\\``)."""
    while line > 1 and line - 2 < len(src_lines) and _continues(src_lines[line - 2]):
        line -= 1
    return line


def _claim_text(src_lines: Sequence[str], first: int) -> tuple[str, str]:
    """``(text, verb)`` of the claim starting on physical line ``first`` (continuations kept)."""
    if not 1 <= first <= len(src_lines):
        return "", ""
    last = first
    while last < len(src_lines) and _continues(src_lines[last - 1]):
        last += 1
    lead = _LEAD.match(src_lines[first - 1])
    return "\n".join(src_lines[first - 1 : last]).strip(), lead.group(1) if lead else ""


def _backstop(limit: float) -> ExecutionError:
    return ExecutionError(
        f"query timed out: no answer after {limit:g}s (engine backstop; the connector did "
        "not enforce timeout_s)",
        code="FL702",
    )


def _runs(jobs: Sequence[tuple[FusedProbe, RenderedProbe | Exception]]) -> bool:
    """Whether any job has a probe to execute (render failures need no connector)."""
    return any(isinstance(p, RenderedProbe) for _, p in jobs)


def _code(message: str, default: str) -> str:
    m = _CODE.match(message)
    return m.group(1) if m else default


def _sample_of(plan: object) -> SampleSpec | None:
    sample = getattr(plan, "sample", None)
    if sample is None:
        sample = getattr(plan, "right_sample", None)
    return sample if isinstance(sample, SampleSpec) else None


def _outcome(
    claim: CheckedClaim,
    verdict: Verdict,
    explanation: str,
    facts: Mapping[str, object],
    evidence: Evidence | None,
    diags: Sequence[Diagnostic],
) -> Outcome:
    return Outcome(
        claim_text=claim.source_text,
        canonical_text=claim.canonical_text,
        semantic_hash=claim.semantic_hash,
        verb=claim.verb,
        verdict=verdict,
        explanation=explanation,
        facts=facts,
        evidence=evidence,
        diagnostics=tuple(d.to_dict() for d in diags),
    )


def _invalid(text: str, verb: str, diags: Sequence[Diagnostic]) -> Outcome:
    errors = [d for d in diags if d.severity is Severity.ERROR]
    explanation = "; ".join(f"{d.code}: {d.message}" for d in errors) or "invalid claim"
    return Outcome(
        claim_text=text,
        canonical_text="",
        semantic_hash="",
        verb=verb,
        verdict=Verdict.INVALID,
        explanation=explanation,
        facts={},
        evidence=None,
        diagnostics=tuple(d.to_dict() for d in diags),
    )


class Checker:
    """Checks FactLang documents against one warehouse (spec 9.3).

    ``dialect`` defaults to the connector's. With a connector and no ``catalog``, names are
    checked (and catalog verbs answered) through ``CachedCatalog(WarehouseCatalog(connector))``
    (spec 6.1). ``concurrency`` probes run at once (a thread pool; ``acheck`` an asyncio
    semaphore). ``max_bytes`` enables the dry-run cost guard (``FL701``). A connector that
    does not enforce ``timeout_s`` is backstopped: no answer ``TIMEOUT_GRACE_S`` after the
    timeout is ``FL702``.
    """

    TIMEOUT_GRACE_S: float = 5.0

    def __init__(
        self,
        connector: Connector | AsyncConnector | None = None,
        *,
        dialect: str | None = None,
        pack: ContractPack | None = None,
        catalog: Catalog | None = None,
        concurrency: int = 4,
        timeout_s: float | None = 60,
        max_bytes: int | None = None,
        fuse: bool = True,
        prefer: Prefer = "native",
    ) -> None:
        if dialect is None and connector is not None:
            dialect = connector.dialect
        if dialect is None:
            raise ValueError("Checker needs a connector or a dialect")
        if isinstance(concurrency, bool) or not isinstance(concurrency, int) or concurrency < 1:
            raise ValueError(f"concurrency must be a positive int, got {concurrency!r}")
        self.connector = connector
        self.dialect = dialect
        self.pack = pack if pack is not None else standard_pack()
        self.concurrency = concurrency
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.fuse = fuse
        self.prefer: Prefer = prefer
        self._default_catalog: Catalog | None = None
        if catalog is None and connector is not None:
            # a warehouse catalog needs a synchronous ``execute``; an async-only connector
            # gets a catalog that knows nothing (names unverified, catalog verbs NO_DATA)
            if isinstance(connector, Connector):
                self._default_catalog = CachedCatalog(
                    WarehouseCatalog(connector, timeout_s=timeout_s)
                )
            else:
                self._default_catalog = NullCatalog()
        self.catalog: Catalog | None = catalog if catalog is not None else self._default_catalog
        self._quote = string_quoter(dialect)

    # -- public API ----------------------------------------------------------------------

    def check(self, text: str) -> Report:
        """Check every claim of ``text``. Raises ``ValueError`` only when a rendered probe
        must run and the Checker has no connector (a configuration error, not a claim outcome)."""
        bag = DiagnosticBag()
        slots = self._front(text, self.catalog, bag)
        for slot in slots:
            if slot.outcome is None and slot.contract is not None and slot.plan is None:
                slot.outcome = self._catalog_outcome(slot, bag)
        jobs = self._jobs([WorkItem(s.plan, s.claim) for s in slots if s.plan is not None])
        probes = [p for _, p in jobs]
        runs = self._run_all(self._need_connector(), probes) if _runs(jobs) else [None] * len(jobs)
        return self._finish(slots, self._results(jobs, runs), bag)

    async def acheck(self, text: str) -> Report:
        bag = DiagnosticBag()
        slots = await asyncio.to_thread(self._front, text, self.catalog, bag)
        for slot in slots:
            if slot.outcome is None and slot.contract is not None and slot.plan is None:
                slot.outcome = await asyncio.to_thread(self._catalog_outcome, slot, bag)
        jobs = self._jobs([WorkItem(s.plan, s.claim) for s in slots if s.plan is not None])
        probes = [p for _, p in jobs]
        runs = (
            await self._arun_all(self._need_connector(), probes)
            if _runs(jobs)
            else [None] * len(jobs)
        )
        return self._finish(slots, self._results(jobs, runs), bag)

    def compile(self, text: str) -> list[RenderedProbe | Outcome]:
        """Each claim's own (unfused) probe, or its outcome when it has none; never executes.

        A catalog verb compiles to the ``MetadataQuery`` probe of its table.
        """
        bag = DiagnosticBag()
        out: list[RenderedProbe | Outcome] = []
        for slot in self._front(text, self._offline_catalog(), bag):
            if slot.outcome is not None:
                out.append(slot.outcome)
                continue
            assert slot.claim is not None
            plan = slot.plan if slot.plan is not None else MetadataQuery(slot.claim.table)
            try:
                out.append(self._renderer()(plan))
            except Exception as exc:
                out.append(self._error(slot, _code(str(exc), "FL500"), str(exc), None, bag))
        return out

    def measure(self, plans: Sequence[ProbePlan]) -> list[Measurement]:
        """Run raw IR primitives (R1); sampled plans allowed. Never raises for a failure."""
        conn = self._need_connector()
        items, keys = self._measure_items(plans)
        jobs = self._jobs(items)
        runs = self._run_all(conn, [p for _, p in jobs])
        return self._measurements(plans, keys, self._results(jobs, runs))

    async def ameasure(self, plans: Sequence[ProbePlan]) -> list[Measurement]:
        conn = self._need_connector()
        items, keys = self._measure_items(plans)
        jobs = self._jobs(items)
        runs = await self._arun_all(conn, [p for _, p in jobs])
        return self._measurements(plans, keys, self._results(jobs, runs))

    def estimate(self, items: str | Sequence[ProbePlan]) -> list[CostEstimate]:
        """Dry-run prices of each probeable claim (text) or plan (R5). Never executes.

        Claims are analysed without a catalog (any catalog may execute metadata queries);
        invalid, unprobeable and catalog claims are skipped.
        """
        pairs: list[tuple[str, ProbePlan]] = []
        if isinstance(items, str):
            bag = DiagnosticBag()
            # no catalog at all: a catalog may run metadata queries, and pricing runs nothing
            for slot in self._front(items, None, bag):
                if slot.plan is not None and slot.claim is not None:
                    pairs.append((slot.claim.semantic_hash, slot.plan))
        else:
            for plan in items:
                try:
                    key = canonical_key(plan)
                except Exception:
                    key = repr(plan)
                pairs.append((key, plan))
        out: list[CostEstimate] = []
        for key, plan in pairs:
            try:
                probe = self._renderer()(plan)
            except Exception:
                out.append(CostEstimate(key, None, None, "unknown"))
                continue
            size: int | None = None
            if self.connector is not None:
                try:
                    size = dry_run_bytes(self.connector, probe)
                except Exception:
                    size = None
            method: Literal["dry_run", "unknown"] = "dry_run" if size is not None else "unknown"
            out.append(CostEstimate(key, probe.inline(self._quote), size, method))
        return out

    # -- front end -----------------------------------------------------------------------

    def _need_connector(self) -> Connector | AsyncConnector:
        if self.connector is None:
            raise ValueError("this Checker has no connector to execute probes on")
        return self.connector

    def _offline_catalog(self) -> Catalog | None:
        """The catalog to analyse with when nothing may be executed."""
        return None if self.catalog is self._default_catalog else self.catalog

    def _renderer(self) -> Callable[[ProbePlan], RenderedProbe]:
        return renderer_for(self.dialect, prefer=self.prefer)

    def _front(self, text: str, catalog: Catalog | None, bag: DiagnosticBag) -> list[_Slot]:
        """Parse and analyse ``text`` claim by claim; slots in source order.

        A ``USE`` directive with errors poisons the namespace: every claim after it, until the
        next valid ``USE``, is ``INVALID`` and carries that directive's diagnostics (its
        namespace is unknown, so resolving its names against the previous one would be wrong).
        """
        parsed = parse(text)
        registry = self.pack.verb_registry()
        items = parsed.document.items
        src_lines = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")
        ranges: list[tuple[int, int]] = []
        for item in items:
            start = item.span.line
            extra = item.line_text.count("\n") if isinstance(item, ast.Claim) else 0
            ranges.append((start, start + extra))

        owned: dict[int, list[Diagnostic]] = {i: [] for i in range(len(items))}
        loose: dict[int, list[Diagnostic]] = {}  # dropped claims, by their first physical line
        for d in parsed.diagnostics:
            line = d.span.line if d.span is not None else None
            idx = next(
                (i for i, (a, b) in enumerate(ranges) if line is not None and a <= line <= b),
                None,
            )
            if idx is not None:
                owned[idx].append(d)
            elif line is not None:
                loose.setdefault(_first_line(src_lines, line), []).append(d)
            else:
                _push(bag, d)

        events: list[tuple[int, int, int]] = [(line, 0, 0) for line in loose]
        events += [(item.span.line, 1, i) for i, item in enumerate(items)]
        events.sort()
        slots: list[_Slot] = []
        current: ast.Directive | None = None
        poison: tuple[Diagnostic, ...] | None = None
        for line, kind, i in events:
            if kind == 0:
                diags = loose[line]
                for d in diags:
                    _push(bag, d)
                errors = tuple(d for d in diags if d.severity is Severity.ERROR)
                if not errors:
                    continue
                claim_text, verb = _claim_text(src_lines, line)
                if verb == "USE":
                    poison = errors
                    continue
                slots.append(_Slot(line, outcome=_invalid(claim_text, verb, diags)))
                continue
            item = items[i]
            if isinstance(item, ast.Directive):
                dbag = DiagnosticBag()
                for d in owned[i]:
                    _push(dbag, d)
                analyze(ast.Document((item,), text), registry, catalog=None, bag=dbag)
                bag.extend(dbag)
                errors = tuple(d for d in dbag if d.severity is Severity.ERROR)
                if errors:
                    poison = errors
                else:
                    current, poison = item, None
                continue
            if poison is not None:
                for d in owned[i]:
                    _push(bag, d)
                outcome = _invalid(item.line_text, item.verb, (*poison, *owned[i]))
                slots.append(_Slot(line, outcome=outcome))
                continue
            cbag = DiagnosticBag()
            for d in owned[i]:
                _push(cbag, d)
            doc_items: tuple[ast.Claim | ast.Directive, ...] = (
                (item,) if current is None else (current, item)
            )
            claims = analyze(ast.Document(doc_items, text), registry, catalog=catalog, bag=cbag)
            bag.extend(cbag)
            claim_diags = tuple(cbag)
            if cbag.has_errors or len(claims) != 1:
                outcome = _invalid(item.line_text, item.verb, claim_diags)
                slots.append(_Slot(line, outcome=outcome))
                continue
            slots.append(self._plan_slot(line, claims[0], claim_diags, bag))
        return slots

    def _plan_slot(
        self, line: int, claim: CheckedClaim, diags: tuple[Diagnostic, ...], bag: DiagnosticBag
    ) -> _Slot:
        slot = _Slot(line, claim=claim, diags=diags)
        contract = self.pack.get(claim.verb)
        if contract is None:  # pragma: no cover - the analyser only accepts registered verbs
            slot.outcome = _invalid(claim.source_text, claim.verb, diags)
            return slot
        slot.contract = contract
        if contract.unprobeable_reason is not None:
            slot.outcome = _outcome(
                claim, Verdict.UNPROBEABLE, contract.unprobeable_reason, {}, None, diags
            )
        elif contract.plan is not None:
            try:
                slot.plan = plan_for(contract, claim)
            except Exception as exc:
                message = str(exc)
                code = _code(message, "")
                if not code:
                    message = f"plan failed: {type(exc).__name__}: {exc}"
                slot.outcome = self._error(slot, code or None, message, None, bag)
        return slot

    def _error(
        self,
        slot: _Slot,
        code: str | None,
        message: str,
        evidence: Evidence | None,
        bag: DiagnosticBag,
    ) -> Outcome:
        assert slot.claim is not None
        diags = list(slot.diags)
        if code is not None:
            d = Diagnostic(code, Severity.ERROR, message, slot.claim.span)
            diags.append(d)
            _push(bag, d)
        return _outcome(slot.claim, Verdict.ERROR, message, {}, evidence, diags)

    # -- catalog verbs -------------------------------------------------------------------

    def _catalog_outcome(self, slot: _Slot, bag: DiagnosticBag) -> Outcome:
        claim, contract = slot.claim, slot.contract
        assert claim is not None and contract is not None and contract.catalog_facts is not None
        catalog = self.catalog if self.catalog is not None else NullCatalog()
        evidence = self._catalog_evidence(claim, catalog)
        try:
            measured = contract.catalog_facts(claim, catalog)
        except ExecutionError as exc:
            return self._error(slot, exc.code, f"catalog lookup failed: {exc}", evidence, bag)
        except Exception as exc:
            message = f"catalog lookup failed: {type(exc).__name__}: {exc}"
            return self._error(slot, None, message, evidence, bag)
        facts: dict[str, object] = {}
        if measured is None:
            verdict, why = Verdict.NO_DATA, "the catalog returned nothing"
        else:
            verdict, why, facts = decide_facts(
                contract.derive, contract.decide, claim, dict(measured)
            )
        if verdict is Verdict.NO_DATA:
            why = f"catalog could not answer: {why}"
        return _outcome(claim, verdict, why, facts, evidence, slot.diags)

    def _catalog_evidence(self, claim: CheckedClaim, catalog: Catalog) -> Evidence:
        try:
            probe = self._renderer()(MetadataQuery(claim.table))
        except Exception:
            return Evidence(dialect=self.dialect)
        if catalog is not self._default_catalog:  # the metadata SQL was not what answered
            return Evidence(dialect=probe.dialect, tier=probe.tier)
        return Evidence(
            sql=probe.inline(self._quote),
            params=tuple(p.value for p in probe.params),
            dialect=probe.dialect,
            tier=probe.tier,
        )

    # -- probes --------------------------------------------------------------------------

    def _jobs(
        self, items: Sequence[WorkItem]
    ) -> list[tuple[FusedProbe, RenderedProbe | Exception]]:
        if not items:
            return []
        fused = optimize(items, fuse=self.fuse)
        try:
            render = self._renderer()
        except Exception as exc:
            return [(fp, exc) for fp in fused]
        out: list[tuple[FusedProbe, RenderedProbe | Exception]] = []
        for fp in fused:
            try:
                out.append((fp, render(fp.plan)))  # type: ignore[arg-type]
            except Exception as exc:
                out.append((fp, exc))
        return out

    def _guard(self, conn: object, probe: RenderedProbe) -> None:
        if self.max_bytes is None:
            return
        size = dry_run_bytes(conn, probe)
        if size is not None and size > self.max_bytes:
            raise ExecutionError(
                f"over cost budget: the probe would scan {size} bytes, max_bytes is "
                f"{self.max_bytes}",
                code="FL701",
            )

    def _execute(self, conn: Connector, probe: RenderedProbe) -> _Run:
        start = time.perf_counter()
        try:
            self._guard(conn, probe)
            result = conn.execute(probe, timeout_s=self.timeout_s)
        except Exception as exc:
            took = (time.perf_counter() - start) * 1000
            return _Run(None, map_error(exc, timeout_s=self.timeout_s), took, None)
        took = (time.perf_counter() - start) * 1000
        return _Run(list(result.rows), None, took, result.bytes_processed)

    async def _aexecute(self, conn: Connector | AsyncConnector, probe: RenderedProbe) -> _Run:
        start = time.perf_counter()
        try:
            if self.max_bytes is not None:
                await asyncio.to_thread(self._guard, conn, probe)
            result = await aexecute(conn, probe, timeout_s=self.timeout_s)
        except Exception as exc:
            took = (time.perf_counter() - start) * 1000
            return _Run(None, map_error(exc, timeout_s=self.timeout_s), took, None)
        took = (time.perf_counter() - start) * 1000
        return _Run(list(result.rows), None, took, result.bytes_processed)

    def _run_all(
        self, conn: Connector | AsyncConnector, probes: Sequence[RenderedProbe | Exception]
    ) -> list[_Run | None]:
        """Execute in a thread pool. Engine-side backstop: a probe still running
        ``timeout_s + TIMEOUT_GRACE_S`` after it started is reported ``FL702`` and abandoned
        (its worker thread is left to the driver; the pool does not wait for it)."""
        todo = [(i, p) for i, p in enumerate(probes) if isinstance(p, RenderedProbe)]
        runs: list[_Run | None] = [None] * len(probes)
        if not todo:
            return runs
        sync: Any = conn
        limit = None if self.timeout_s is None else self.timeout_s + self.TIMEOUT_GRACE_S
        started: dict[int, float] = {}

        def work(i: int, probe: RenderedProbe) -> _Run:
            started[i] = time.monotonic()
            return self._execute(sync, probe)

        pool = ThreadPoolExecutor(max_workers=min(self.concurrency, len(todo)))
        try:
            futures: dict[Future[_Run], int] = {pool.submit(work, i, p): i for i, p in todo}
            pending = set(futures)
            while pending:
                poll = None if limit is None else min(0.25, limit)
                done, pending = wait(pending, timeout=poll, return_when=FIRST_COMPLETED)
                for f in done:
                    runs[futures[f]] = f.result()
                if limit is None:
                    continue
                now = time.monotonic()
                for f in list(pending):
                    i = futures[f]
                    began = started.get(i)
                    if began is not None and now - began > limit:
                        pending.discard(f)
                        runs[i] = _Run(None, _backstop(limit), (now - began) * 1000, None)
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return runs

    async def _arun_all(
        self, conn: Connector | AsyncConnector, probes: Sequence[RenderedProbe | Exception]
    ) -> list[_Run | None]:
        gate = asyncio.Semaphore(self.concurrency)

        async def one(p: RenderedProbe | Exception) -> _Run | None:
            if not isinstance(p, RenderedProbe):
                return None
            async with gate:
                if self.timeout_s is None:
                    return await self._aexecute(conn, p)
                limit = self.timeout_s + self.TIMEOUT_GRACE_S
                try:
                    return await asyncio.wait_for(self._aexecute(conn, p), limit)
                except asyncio.TimeoutError:
                    return _Run(None, _backstop(limit), limit * 1000, None)

        return list(await asyncio.gather(*(one(p) for p in probes)))

    def _results(
        self,
        jobs: Sequence[tuple[FusedProbe, RenderedProbe | Exception]],
        runs: Sequence[_Run | None],
    ) -> dict[str, _Result]:
        out: dict[str, _Result] = {}
        for (fp, probe), run in zip(jobs, runs, strict=True):
            keys = [k for k, _ in fp.members]
            if not isinstance(probe, RenderedProbe):
                message = (
                    str(probe)
                    if isinstance(probe, RenderError)
                    else (f"FL500 render failed: {type(probe).__name__}: {probe}")
                )
                for key in keys:
                    out[key] = _Result(None, _code(message, "FL500"), message, None)
                continue
            assert run is not None
            member_rows: dict[str, list[dict[str, object]]] | None = None
            error = run.error
            if error is None:
                try:
                    member_rows = split(fp, run.rows or [])
                except Exception as exc:
                    error = ExecutionError(
                        f"result shape: {type(exc).__name__}: {exc}", code="FL700"
                    )
            sql = probe.inline(self._quote)
            params = tuple(p.value for p in probe.params)
            member_plans = dict(self._member_plans(fp))
            for key in keys:
                sample = _sample_of(member_plans[key])
                evidence = Evidence(
                    sql=sql,
                    params=params,
                    dialect=probe.dialect,
                    tier=probe.tier,
                    duration_ms=run.duration_ms,
                    bytes_processed=run.bytes_processed,
                    fused_with=tuple(k for k in keys if k != key),
                    sample=sample,
                    method=sample_method(sample),
                )
                if error is not None:
                    out[key] = _Result(None, error.code, str(error), evidence)
                else:
                    assert member_rows is not None
                    out[key] = _Result(member_rows[key], None, None, evidence)
        return out

    @staticmethod
    def _member_plans(fp: FusedProbe) -> list[tuple[str, object]]:
        if isinstance(fp.plan, FusedAggregate):
            return [(k, m) for (k, _), m in zip(fp.members, fp.plan.members, strict=True)]
        return [(k, fp.plan) for k, _ in fp.members]

    def _finish(
        self, slots: Sequence[_Slot], results: Mapping[str, _Result], bag: DiagnosticBag
    ) -> Report:
        outcomes: list[Outcome] = []
        for slot in slots:
            if slot.outcome is not None:
                outcomes.append(slot.outcome)
                continue
            claim, contract, plan = slot.claim, slot.contract, slot.plan
            assert claim is not None and contract is not None and plan is not None
            res = results[claim.semantic_hash]
            if res.rows is None:
                message = res.message or "probe failed"
                outcomes.append(self._error(slot, res.code, message, res.evidence, bag))
                continue
            verdict, why, facts = judge_rows(
                plan.arity, res.rows, contract.derive, contract.decide, claim
            )
            outcomes.append(_outcome(claim, verdict, why, facts, res.evidence, slot.diags))
        return Report(tuple(outcomes), bag)

    # -- measure -------------------------------------------------------------------------

    @staticmethod
    def _measure_items(plans: Sequence[ProbePlan]) -> tuple[list[WorkItem], list[str]]:
        items: list[WorkItem] = []
        keys: list[str] = []
        for i, plan in enumerate(plans):
            try:
                key = canonical_key(plan)
            except Exception:
                key = f"#unkeyed-{i}"
            keys.append(key)
            items.append(WorkItem(plan, key=key))
        return items, keys

    @staticmethod
    def _measurements(
        plans: Sequence[ProbePlan], keys: Sequence[str], results: Mapping[str, _Result]
    ) -> list[Measurement]:
        out: list[Measurement] = []
        for plan, key in zip(plans, keys, strict=True):
            res = results[key]
            if res.rows is None:
                err = Diagnostic(res.code or "FL700", Severity.ERROR, res.message or "", None)
                out.append(Measurement(plan, None, Verdict.ERROR, res.evidence, err))
                continue
            try:
                payload = payload_for(plan.arity, res.rows)
            except Exception as exc:
                message = f"FL700 result shape: {type(exc).__name__}: {exc}"
                err = Diagnostic("FL700", Severity.ERROR, message, None)
                out.append(Measurement(plan, None, Verdict.ERROR, res.evidence, err))
                continue
            hint = Verdict.NO_DATA if payload is None else None
            out.append(Measurement(plan, payload, hint, res.evidence, None))
        return out


__all__ = ["Checker", "CostEstimate", "Measurement", "Report", "string_quoter"]
