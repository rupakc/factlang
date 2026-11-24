"""Three-valued judgement of derived facts (spec 1.1 items 8-10, 7; layer 9)."""

from __future__ import annotations

from factlang.judge.outcome import Evidence, Outcome, Verdict, json_safe
from factlang.judge.predicate import (
    And,
    Compare,
    CompareUsingClaimOp,
    Not,
    Or,
    Predicate,
    WhenClaimHas,
    Within,
    compare,
)
from factlang.judge.run import NO_ROWS, Derive, decide_facts, payload_for

__all__ = [
    "NO_ROWS",
    "And",
    "Compare",
    "CompareUsingClaimOp",
    "Derive",
    "Evidence",
    "Not",
    "Or",
    "Outcome",
    "Predicate",
    "Verdict",
    "WhenClaimHas",
    "Within",
    "compare",
    "decide_facts",
    "json_safe",
    "payload_for",
]
