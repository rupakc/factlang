"""VALUES: ValueMembership rows -> undeclared/coverage facts (spec 6.2, 8.1)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from packs_helpers import Verdict, assert_pack_complete, build_claim, judge

from factlang.contract import ContractPack, plan_for
from factlang.ir import ValueMembership
from factlang.packs.standard.values import CONTRACT

SRC = "VALUES orders.status IN ['paid', 'open']"


def test_contract_is_complete() -> None:
    pack = ContractPack("t", "1")
    pack.register(CONTRACT)
    assert_pack_complete(pack)


def test_attr_specs() -> None:
    by = {a.name: a for a in CONTRACT.spec.attrs}
    assert (by["IN"].type, by["IN"].required, by["IN"].ops) == ("VALUE_LIST", True, ())
    assert (by["SPLIT"].type, by["SPLIT"].required) == ("SEPARATOR", False)
    assert (by["ALLOW"].type, by["ALLOW"].required, by["ALLOW"].ops) == ("RATIO", False, ("<=",))
    assert CONTRACT.spec.filterable


def test_plan() -> None:
    c = build_claim("VALUES orders.status IN ['paid', 1, 2.50] SPLIT ':' WHERE region = 'EU'")
    p = plan_for(CONTRACT, c)
    assert isinstance(p, ValueMembership)
    assert p.column == c.column.column and p.declared == ("paid", "1", "2.5")
    assert p.split == ":" and p.filter == c.filter and p.report_limit == 20


def test_plan_empty_list_keeps_claim_semantics() -> None:
    p = plan_for(CONTRACT, build_claim("VALUES orders.status IN []"))
    assert isinstance(p, ValueMembership) and p.declared == () and p.split is None


def rows(non_null: object, declared: object, und: list[tuple[str, object]]) -> dict[str, object]:
    out: list[dict[str, object]] = [
        {
            "kind": "summary",
            "non_null": non_null,
            "declared_rows": declared,
            "undeclared_distinct": len(und),
            "value": None,
            "freq": None,
        }
    ]
    out += [
        {
            "kind": "undeclared",
            "non_null": None,
            "declared_rows": None,
            "undeclared_distinct": None,
            "value": v,
            "freq": n,
        }
        for v, n in und
    ]
    return {"rows": out}


def test_facts_normal() -> None:
    v, _, f = judge(CONTRACT, SRC, rows(100, 97, [("void", 2), ("Paid", 1)]))
    assert v is Verdict.FAIL
    assert f["undeclared_rows"] == 3 and f["undeclared_distinct"] == 2
    assert f["coverage"] == Decimal("0.97") and f["undeclared_share"] == Decimal("0.03")
    assert f["top_undeclared"] == ({"value": "void", "count": 2}, {"value": "Paid", "count": 1})


def test_top_undeclared_is_reranked_freq_desc_value_asc() -> None:
    und = [("a", 1), ("zeta", 5), ("beta", 5), ("x", Decimal(7))]
    _, _, f = judge(CONTRACT, SRC, rows(100, 82, list(reversed(und))))
    assert f["top_undeclared"] == (
        {"value": "x", "count": 7},
        {"value": "beta", "count": 5},
        {"value": "zeta", "count": 5},
        {"value": "a", "count": 1},
    )
    assert all(type(d["count"]) is int for d in f["top_undeclared"])  # type: ignore[index]


def test_all_declared_passes() -> None:
    v, _, f = judge(CONTRACT, SRC, rows(Decimal(50), Decimal(50), []))
    assert v is Verdict.PASS and f["undeclared_rows"] == 0 and f["coverage"] == 1
    assert type(f["undeclared_rows"]) is int and type(f["undeclared_distinct"]) is int
    assert f["top_undeclared"] == ()


@pytest.mark.parametrize(
    ("allow", "verdict"), [("3%", Verdict.PASS), ("2.9%", Verdict.FAIL), ("0", Verdict.FAIL)]
)
def test_allow(allow: str, verdict: Verdict) -> None:
    v, _, _ = judge(CONTRACT, f"{SRC} ALLOW <= {allow}", rows(100, 97, [("void", 3)]))
    assert v is verdict


@pytest.mark.parametrize("extra", ["", " ALLOW <= 5%"])
def test_no_non_null_values_is_no_data(extra: str) -> None:
    v, _, f = judge(CONTRACT, SRC + extra, rows(0, 0, []))
    assert v is Verdict.NO_DATA
    assert f["coverage"] is None and f["undeclared_rows"] is None
    assert f["undeclared_share"] is None


def test_empty_declared_list_every_row_undeclared() -> None:
    v, _, f = judge(CONTRACT, "VALUES orders.status IN []", rows(4, 0, [("a", 3), ("b", 1)]))
    assert v is Verdict.FAIL and f["undeclared_rows"] == 4 and f["coverage"] == 0
    assert f["undeclared_share"] == 1


def test_missing_summary_is_error() -> None:
    v, _, _ = judge(CONTRACT, SRC, {"rows": [{"kind": "undeclared", "value": "x", "freq": 1}]})
    assert v is Verdict.ERROR
