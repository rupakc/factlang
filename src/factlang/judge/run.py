"""Shaping probe rows and judging them (spec 1.1 items 4, 8, 10). Judging never raises."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from factlang.ir.metrics import Arity
from factlang.judge.outcome import Verdict
from factlang.judge.predicate import Predicate
from factlang.semantics.checked import CheckedClaim

Row = Mapping[str, object]
Payload = Mapping[str, object]
Derive = Callable[[Payload, CheckedClaim], Mapping[str, object]]

NO_ROWS = "the probe returned no rows"


def payload_for(arity: Arity, rows: Sequence[Row]) -> dict[str, object] | None:
    """Shape result rows by the plan's arity: one row, or ``{"rows": rows}``; none -> ``None``."""
    if not rows:
        return None
    if arity is Arity.SINGLE_ROW:
        return dict(rows[0])
    if arity is Arity.ROW_SET:
        return {"rows": list(rows)}
    raise ValueError(f"unknown arity {arity!r}")


def decide_facts(
    derive: Derive,
    decide: Predicate,
    claim: CheckedClaim,
    payload: Payload | None,
) -> tuple[Verdict, str, dict[str, object]]:
    """Derive facts from ``payload`` and decide them: ``(verdict, explanation, facts)``.

    Never raises: a failing ``derive`` or ``decide`` is ``ERROR`` (a defect), an
    undecidable predicate or an empty payload is ``NO_DATA``.
    """
    if payload is None:
        return Verdict.NO_DATA, NO_ROWS, {}
    try:
        derived = derive(payload, claim)
        if not isinstance(derived, Mapping):
            raise TypeError(f"derive must return a mapping, got {type(derived).__name__}")
        facts = dict(derived)
    except Exception as exc:
        return Verdict.ERROR, f"derive failed: {type(exc).__name__}: {exc}", {}
    try:
        ok, why = decide.evaluate(facts, claim)
    except Exception as exc:
        return Verdict.ERROR, f"decide failed: {type(exc).__name__}: {exc}", facts
    if ok is None:
        return Verdict.NO_DATA, why, facts
    return (Verdict.PASS if ok else Verdict.FAIL), why, facts


__all__ = ["NO_ROWS", "Derive", "Payload", "Row", "decide_facts", "payload_for"]
