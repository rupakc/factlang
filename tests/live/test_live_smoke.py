"""Smoke checks per ``native`` dialect against a real warehouse (opt-in, see ``conftest.py``)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from live_targets import LiveTarget

from factlang.engine import Checker
from factlang.ir import SampleSpec, ValueMembership
from factlang.judge import Verdict
from factlang.semantics.catalog import NullCatalog
from factlang.semantics.refs import Ident, TableRef

pytestmark = pytest.mark.live


def _table(dotted: str) -> TableRef:
    parts = [Ident(p) for p in dotted.split(".")]
    padded: list[Ident | None] = [None] * (3 - len(parts)) + list(parts)
    return TableRef(padded[0], padded[1], parts[-1])


def test_claims_run_and_report_the_native_tier(live: LiveTarget) -> None:
    report = Checker(live.connector).check(
        f"ROWS {live.table} VALUE >= 1\nEXISTS {live.table} COLUMN {live.column}"
    )
    assert [o.verdict for o in report.outcomes] == [Verdict.PASS, Verdict.PASS], [
        o.explanation for o in report.outcomes
    ]
    evidence = report.outcomes[0].evidence
    assert evidence is not None and evidence.tier == "native"
    assert evidence.dialect == live.dialect


def test_hostile_literal_is_bound(live: LiveTarget) -> None:
    claim = f"COUNT {live.table} VALUE == 0 WHERE {live.column} = 'x''; DROP TABLE t; --'"
    (outcome,) = Checker(live.connector, catalog=NullCatalog()).check(claim).outcomes
    assert outcome.verdict is Verdict.PASS, outcome.explanation


def test_keyhash_sample_repeats_with_the_same_seed(live: LiveTarget) -> None:
    column = Ident(live.column)
    spec = SampleSpec("keyhash", Decimal("0.1"), 7, (column,))
    plan = ValueMembership(_table(live.table), column, declared=(), report_limit=1000, sample=spec)
    (first,) = Checker(live.connector).measure([plan])
    (again,) = Checker(live.connector).measure([plan])
    assert first.error is None, first.error
    assert first.payload == again.payload
