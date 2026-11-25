"""Print the DuckDB verb matrix's outcomes as JSON (one run of ``test_verbs_duckdb``'s cases).

Run as a script by ``test_optimized_parity.py``, once normally and once under ``python -O``,
so the two runs can be compared: an ``assert`` that carries behaviour shows up as a
difference. After the matrix come a few claims checked through a ``CachedCatalog`` over a
catalog that breaks the protocol, and claims of a custom verb whose plan returns no plan
(``None``, or an object that is not a probe plan): the guards on such host values must hold
under ``-O`` too.
Wall-clock dependent values (the ``Approx`` facts of the expectations, and the
explanation of a claim that has one) are masked; everything else is compared exactly.
This module deliberately uses no ``assert`` itself.
"""

from __future__ import annotations

import json
import sys
from typing import Any

from expectations import CASES, COMPOSITE_JOIN, COVERAGE_FAIL, Approx, Case
from shop import make_shop

import factlang as fl
from factlang.connectors import DBAPIConnector
from factlang.engine import Checker
from factlang.semantics.catalog import CachedCatalog, NullCatalog
from factlang.semantics.refs import TableRef

MASK = "<wall-clock>"


class _Lying:
    """A catalog whose answers have the wrong types (a ``str`` flag, a list of pairs)."""

    def table_exists(self, ref: TableRef) -> object:
        return "yes" if ref.table.text == "orders" else True

    def columns(self, ref: TableRef) -> object:
        return [("shipped_at", "timestamp"), ("id", "integer")]


HOSTILE = (
    "EXISTS orders",
    "EXISTS customers COLUMN id",
    "TYPE customers.id IS integer",
)


BAD_PLAN = ("NONE_PLAN orders.amount", "STR_PLAN orders.amount WHERE status = 'paid'")


def _bad_plan_pack() -> fl.ContractPack:
    """Custom verbs whose plan is a host bug: it returns ``None`` or a string."""
    pack = fl.standard_pack().extend("bad-plans", "1.0")
    for name, returned in (("NONE_PLAN", None), ("STR_PLAN", "SELECT 1")):
        verb = pack.verb(name, summary="x", operands=[fl.Operand.column()], filterable=True)
        verb(lambda claim, returned=returned: returned)
        verb.derive(lambda payload, claim: {"min": payload["min_value"]})
        verb.facts("min")
        verb.decide(fl.Compare("min", ">", value=0))
    return pack


def _bad_plan_outcome(conn: DBAPIConnector, claim: str) -> dict[str, Any]:
    pack = _bad_plan_pack()
    (o,) = Checker(conn, pack=pack, catalog=NullCatalog()).check(claim).outcomes
    (c,) = Checker(dialect="duckdb", pack=pack).compile(claim)
    compiled = c.explanation if isinstance(c, fl.Outcome) else f"probe: {c.sql_template}"
    return {
        "claim": claim,
        "outcomes": [{"verdict": o.verdict.name, "explanation": o.explanation}],
        "compiled": compiled,
    }


def _outcome(conn: DBAPIConnector, case: Case | str) -> dict[str, Any]:
    kw: dict[str, Any] = {}
    clocked: set[str] = set()
    if isinstance(case, str) and case in BAD_PLAN:
        return _bad_plan_outcome(conn, case)
    if isinstance(case, str):
        claim, lying = case, _Lying()
        kw["catalog"] = CachedCatalog(lying)  # type: ignore[arg-type]
    else:
        claim = case.claim
        if case.catalog == "null":
            kw["catalog"] = NullCatalog()
        clocked = {k for k, v in case.facts.items() if isinstance(v, Approx)}
    report = Checker(conn, **kw).check(claim)
    out: list[dict[str, Any]] = []
    for o in report.outcomes:
        out.append(
            {
                "verb": o.verb,
                "verdict": o.verdict.name,
                "facts": {k: MASK if k in clocked else repr(v) for k, v in o.facts.items()},
                "explanation": MASK if clocked else o.explanation,
                "codes": sorted(str(d.get("code")) for d in o.diagnostics),
                "sql": o.evidence.sql if o.evidence is not None else None,
            }
        )
    return {
        "claim": claim,
        "outcomes": out,
        "report_codes": sorted(d.code for d in report.diagnostics),
    }


def main() -> int:
    import duckdb

    con = duckdb.connect()
    try:
        make_shop(con)
        conn = DBAPIConnector(con, "duckdb")
        cases = (*CASES, *COMPOSITE_JOIN, COVERAGE_FAIL)
        rows = [_outcome(conn, case) for case in (*cases, *HOSTILE, *BAD_PLAN)]
    finally:
        con.close()
    json.dump({"optimize": sys.flags.optimize, "cases": rows}, sys.stdout, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
