from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping
from decimal import Decimal

from factlang.diagnostics import Span
from factlang.ir import Arity
from factlang.judge import (
    Compare,
    Evidence,
    Outcome,
    Verdict,
    decide_facts,
    judge_rows,
    payload_for,
)
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.refs import Ident, TableRef

CLAIM = CheckedClaim(
    verb="UNIQUE",
    operands=(TableRef(None, None, Ident("orders")),),
    attrs={},
    filter=None,
    canonical_text="UNIQUE orders",
    semantic_hash="sha256:x",
    span=Span(1, 1, 2),
    source_text="UNIQUE orders",
    comment=None,
)
DECIDE = Compare("max_rows_per_key", "<=", value=1)


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {"max_rows_per_key": payload["max_rows_per_key"]}


def test_verdict_values() -> None:
    assert {v.name: v.value for v in Verdict} == {
        "PASS": "pass",
        "FAIL": "fail",
        "NO_DATA": "no_data",
        "ERROR": "error",
        "UNPROBEABLE": "unprobeable",
        "INVALID": "invalid",
    }
    assert Verdict.PASS == "pass"


def test_payload_for() -> None:
    rows = [{"a": 1}, {"a": 2}]
    assert payload_for(Arity.ROW_SET, rows) == {"rows": rows}
    assert payload_for(Arity.SINGLE_ROW, []) is None
    assert payload_for(Arity.ROW_SET, []) is None
    assert payload_for(Arity.SINGLE_ROW, rows[:1]) == {"a": 1}


def test_payload_for_single_row_with_many_rows_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="expected one row, got 2"):
        payload_for(Arity.SINGLE_ROW, [{"a": 1}, {"a": 2}])


def test_judge_rows_maps_shape_error_to_error() -> None:
    rows = [{"max_rows_per_key": 1}, {"max_rows_per_key": 2}]
    v, why, facts = judge_rows(Arity.SINGLE_ROW, rows, derive, DECIDE, CLAIM)
    assert v is Verdict.ERROR and "expected one row, got 2" in why and facts == {}
    v, _, _ = judge_rows(Arity.SINGLE_ROW, rows[:1], derive, DECIDE, CLAIM)
    assert v is Verdict.PASS
    v, _, _ = judge_rows(Arity.SINGLE_ROW, [], derive, DECIDE, CLAIM)
    assert v is Verdict.NO_DATA


def test_decide_pass_fail_no_data() -> None:
    v, why, facts = decide_facts(derive, DECIDE, CLAIM, {"max_rows_per_key": 1})
    assert v is Verdict.PASS and facts == {"max_rows_per_key": 1} and "1" in why
    v, why, _ = decide_facts(derive, DECIDE, CLAIM, {"max_rows_per_key": 3})
    assert v is Verdict.FAIL and why == "max_rows_per_key 3 > 1"
    v, why, _ = decide_facts(derive, DECIDE, CLAIM, {"max_rows_per_key": None})
    assert v is Verdict.NO_DATA and why == "max_rows_per_key was not measured"
    v, _, facts = decide_facts(derive, DECIDE, CLAIM, None)
    assert v is Verdict.NO_DATA and facts == {}


def test_judge_with_raising_derive_is_error() -> None:
    def boom(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
        raise ZeroDivisionError("division by zero")

    v, why, facts = decide_facts(boom, DECIDE, CLAIM, {"max_rows_per_key": 1})
    assert v is Verdict.ERROR
    assert why == "derive failed: ZeroDivisionError: division by zero"
    assert facts == {}


def test_judge_with_raising_decide_is_error() -> None:
    v, why, facts = decide_facts(
        derive, Compare("max_rows_per_key", "<", value=1), CLAIM, {"max_rows_per_key": "x"}
    )
    assert v is Verdict.ERROR and why.startswith("decide failed: TypeError")
    assert facts == {"max_rows_per_key": "x"}


def test_judge_with_non_mapping_derive_is_error() -> None:
    v, why, _ = decide_facts(lambda p, c: 3, DECIDE, CLAIM, {"x": 1})  # type: ignore[arg-type,return-value]
    assert v is Verdict.ERROR and why.startswith("derive failed")


def test_outcome_to_dict_is_json_safe() -> None:
    ev = Evidence(
        sql="SELECT 1",
        params=(Decimal("0.5"), dt.date(2026, 1, 2), "x"),
        dialect="duckdb",
        tier="verified",
        duration_ms=1.5,
        bytes_processed=10,
        fused_with=("sha256:a",),
    )
    naive = dt.datetime(2026, 1, 2, 3, 4, 5)
    aware = dt.datetime(2026, 1, 2, 4, 4, 5, tzinfo=dt.timezone(dt.timedelta(hours=1)))
    o = Outcome(
        claim_text="UNIQUE orders.id",
        canonical_text="UNIQUE orders.id",
        semantic_hash="sha256:x",
        verb="UNIQUE",
        verdict=Verdict.PASS,
        explanation="max_rows_per_key 1 <= 1",
        facts={
            "rate": Decimal("0.25"),
            "seen": naive,
            "seen_aware": aware,
            "top": ({"value": "a", "count": 2},),
            "none": None,
        },
        evidence=ev,
        diagnostics=({"code": "FL313", "span": (1, 2)},),
    )
    d = o.to_dict()
    assert type(d["verdict"]) is str and d["verdict"] == "pass"
    text = json.dumps(d)  # must not raise
    back = json.loads(text)
    assert back["verdict"] == "pass"
    assert back["facts"]["rate"] == "0.25"
    assert back["facts"]["seen"] == "2026-01-02T03:04:05+00:00"
    assert back["facts"]["seen_aware"] == "2026-01-02T03:04:05+00:00"
    assert back["facts"]["top"] == [{"value": "a", "count": 2}]
    assert back["evidence"]["params"] == ["0.5", "2026-01-02", "x"]
    assert back["evidence"]["fused_with"] == ["sha256:a"]
    assert back["diagnostics"] == [{"code": "FL313", "span": [1, 2]}]
    o2 = Outcome("x", "x", "h", "NOTE", Verdict.UNPROBEABLE, "semantic", {}, None, ())
    assert json.loads(json.dumps(o2.to_dict()))["evidence"] is None


def test_evidence_sample_and_method() -> None:
    from factlang.ir import SampleSpec
    from factlang.judge import sample_method
    from factlang.semantics.refs import Ident

    assert Evidence().method == "exact" and Evidence().sample is None
    s = SampleSpec("keyhash", Decimal("0.01"), 7, (Ident("id"),))
    assert sample_method(None) == "exact"
    assert sample_method(s) == "sampled:keyhash:0.01:7"
    ev = Evidence(sample=s, method=sample_method(s))
    o = Outcome("x", "x", "h", "ROWS", Verdict.PASS, "", {}, ev, ())
    back = json.loads(json.dumps(o.to_dict()))["evidence"]
    assert back["method"] == "sampled:keyhash:0.01:7"
    assert back["sample"]["fraction"] == "0.01" and back["sample"]["seed"] == 7


def test_verdict_str_and_format_are_its_value() -> None:
    from factlang.judge import Verdict

    for v in Verdict:
        assert str(v) == v.value and f"{v}" == v.value and format(v, "") == v.value
    assert f"{Verdict.NO_DATA:>8}" == " no_data"
    assert Verdict("pass") is Verdict.PASS and Verdict.PASS == "pass"
