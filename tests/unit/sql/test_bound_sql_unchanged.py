"""Inline spelling changes never touch bound-parameter SQL (0.1.3 spec 3.2).

``tests/fixtures/bound_sql_templates.json`` was recorded from 0.1.2 (``00b76c6``, before the
inline timestamp/boolean spellings, the bridge literal table and the precision caps changed).
For every representative claim -- every valid standard-pack example plus filters holding
timestamp, date, boolean, decimal and large-integer literals -- and for every native dialect
and every bridged dialect whose inline spelling changed, it holds a digest of the rendered
probes' ``sql_template`` and parameters (name, ``repr`` of the value, family). They must stay
byte-identical. Regenerate only on a deliberate change to bound SQL:
``python tests/unit/sql/test_bound_sql_unchanged.py > tests/fixtures/bound_sql_templates.json``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

import factlang as fl
from factlang.packs.standard import standard_pack
from factlang.sql import native_dialects

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "bound_sql_templates.json"
BRIDGED = ["presto", "athena", "dune", "hive", "spark", "spark2", "teradata", "exasol"]
DIALECTS = [*native_dialects(), *BRIDGED]

EXTRA_CLAIMS = [
    "COUNT orders VALUE >= 0 WHERE placed_at >= TIMESTAMP '2025-01-31T12:00:00.000250Z'",
    "COUNT orders VALUE >= 0 WHERE placed_at < TIMESTAMP '2025-01-31T12:00:00+05:00'",
    "COUNT orders VALUE >= 0 WHERE day = DATE '2025-01-31'",
    "COUNT orders VALUE >= 0 WHERE deleted = TRUE AND paid = FALSE",
    "COUNT orders VALUE >= 0 WHERE amount > 12345678901234567890123456789012345678901.5",
    "COUNT orders VALUE >= 0 WHERE id > 99999999999999999999",
    "COUNT orders VALUE >= 0 WHERE ratio < 0.1",
    "SUM orders.amount VALUE == 1E+50",
    "MAX events.at VALUE <= TIMESTAMP '2025-01-31T12:00:00Z'",
]


def claims() -> list[str]:
    examples = [e.source for c in standard_pack().contracts() for e in c.examples if e.valid]
    return list(dict.fromkeys(examples + EXTRA_CLAIMS))


def rendered(claim: str, dialect: str) -> Any:
    """The claim's probes as data: ``sql_template`` + params, or its compile error."""
    try:
        probes = fl.compile(claim, dialect=dialect)
    except ValueError as exc:
        return {"error": str(exc).splitlines()[0]}
    return [
        {
            "sql_template": p.sql_template,
            "params": [[q.name, repr(q.value), q.family] for q in p.params],
        }
        for p in probes
    ]


def digest(claim: str, dialect: str) -> str:
    text = json.dumps(rendered(claim, dialect), sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def snapshot() -> dict[str, Any]:
    return {claim: {d: digest(claim, d) for d in DIALECTS} for claim in claims()}


def recorded() -> dict[str, dict[str, str]]:
    data: dict[str, dict[str, str]] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def test_the_fixture_covers_every_representative_claim() -> None:
    assert list(recorded()) == claims()
    families = {
        param[2]
        for claim in EXTRA_CLAIMS
        for probe in rendered(claim, "duckdb")
        for param in probe["params"]
    }
    assert {"timestamp", "date", "boolean", "decimal", "integer"} <= families, families


@pytest.mark.parametrize("dialect", DIALECTS)
def test_bound_sql_templates_are_byte_identical_to_0_1_2(dialect: str) -> None:
    if dialect not in native_dialects():
        pytest.importorskip("sqlglot")
    changed = [
        (claim, rendered(claim, dialect))
        for claim, by_dialect in recorded().items()
        if digest(claim, dialect) != by_dialect[dialect]
    ]
    assert not changed, changed


if __name__ == "__main__":  # pragma: no cover - fixture regeneration
    json.dump(snapshot(), sys.stdout, indent=1)
    sys.stdout.write("\n")
