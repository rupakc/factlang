from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import ClassVar

import pytest

from factlang import ir
from factlang.ir import (
    ALIASES,
    PRIMITIVE_LOWERINGS,
    Arity,
    ColumnAggregate,
    CompositeKeyAggregate,
    KeyOverlap,
    MetadataQuery,
    Metric,
    PatternCoverage,
    SampleSpec,
    ValueMembership,
    canonical_key,
    is_sampled,
    register_primitive,
    unregister_primitive,
)
from factlang.semantics.filters import FCompare, Param
from factlang.semantics.refs import ColumnRef, Ident, TableRef

T = TableRef(None, None, Ident("orders"))
U = TableRef(None, Ident("shop"), Ident("customers"))
C = Ident("id")


def col(t: TableRef, name: str) -> ColumnRef:
    return ColumnRef(t, Ident(name))


def flt(v: int = 1, name: str = "p0") -> FCompare:
    return FCompare(Ident("status"), "=", Param(name, v, "integer"))


# --- metrics ------------------------------------------------------------------------


def test_aliases_table_is_exactly_the_wire_format() -> None:
    assert {m.name: a for m, a in ALIASES.items()} == {
        "ROW_COUNT": "row_count",
        "NON_NULL": "non_null",
        "DISTINCT_KEYS": "distinct_keys",
        "MAX_PER_KEY": "max_rows_per_key",
        "MIN_VALUE": "min_value",
        "MAX_VALUE": "max_value",
        "SUM": "sum_value",
        "AVG": "avg_value",
        "DUPLICATE_KEYS": "duplicate_keys",
        "NOW": "server_now",
    }
    assert set(ALIASES) == set(Metric)
    assert "rows" not in ALIASES.values()
    with pytest.raises(TypeError):
        ALIASES[Metric.SUM] = "x"  # type: ignore[index]


def test_arity_members() -> None:
    assert [a.name for a in Arity] == ["SINGLE_ROW", "ROW_SET"]


# --- arity / result columns ---------------------------------------------------------


def test_arity_and_result_columns() -> None:
    ca = ColumnAggregate(T, C, (Metric.ROW_COUNT, Metric.NON_NULL))
    assert ca.arity is Arity.SINGLE_ROW
    assert ca.result_columns() == ("row_count", "non_null")
    ck = CompositeKeyAggregate(T, (C, Ident("shop_id")), (Metric.MAX_PER_KEY,))
    assert ck.arity is Arity.SINGLE_ROW and ck.result_columns() == ("max_rows_per_key",)
    vm = ValueMembership(T, C, ("a",))
    assert vm.arity is Arity.ROW_SET
    assert set(vm.result_columns()) == {
        "kind",
        "non_null",
        "declared_rows",
        "undeclared_distinct",
        "value",
        "freq",
    }
    ko = KeyOverlap((col(T, "cid"),), (col(U, "id"),))
    assert ko.arity is Arity.SINGLE_ROW
    assert ko.result_columns() == (
        "distinct_left",
        "matched",
        "distinct_right",
        "left_non_null",
        "matched_left_rows",
    )
    kom = KeyOverlap((col(T, "cid"),), (col(U, "id"),), measure_multiplicity=True)
    assert kom.result_columns()[-2:] == ("max_left_per_key", "max_right_per_key")
    pc = PatternCoverage(T, C, "%@%")
    assert pc.arity is Arity.SINGLE_ROW and pc.result_columns() == ("non_null", "matching")
    mq = MetadataQuery(T)
    assert mq.arity is Arity.ROW_SET and mq.result_columns() == ("column_name", "data_type")


def test_sampled_result_columns_add_sampled_rows() -> None:
    s = SampleSpec("system", Decimal("0.1"))
    assert ColumnAggregate(T, None, (Metric.ROW_COUNT,), sample=s).result_columns() == (
        "row_count",
        "sampled_rows",
    )
    ko = KeyOverlap((col(T, "cid"),), (col(U, "id"),), sample=s, right_sample=s)
    assert ko.result_columns()[-2:] == ("sampled_left_rows", "sampled_right_rows")


# --- validation ---------------------------------------------------------------------


def test_column_aggregate_validation() -> None:
    with pytest.raises(ValueError, match="metric"):
        ColumnAggregate(T, C, ())
    for m in (Metric.MAX_PER_KEY, Metric.DUPLICATE_KEYS):
        with pytest.raises(ValueError, match="column"):
            ColumnAggregate(T, None, (m,))
    with pytest.raises(ValueError, match="duplicate"):
        ColumnAggregate(T, C, (Metric.SUM, Metric.SUM))
    ColumnAggregate(T, None, (Metric.ROW_COUNT, Metric.NOW))  # table-level is fine


def test_column_metrics_need_a_column() -> None:
    for m in (Metric.NON_NULL, Metric.DISTINCT_KEYS, Metric.MIN_VALUE, Metric.SUM, Metric.AVG):
        with pytest.raises(ValueError, match="column"):
            ColumnAggregate(T, None, (m,))


def test_list_and_columnref_inputs_are_normalised() -> None:
    a = ColumnAggregate(T, col(T, "id"), [Metric.MIN_VALUE])  # type: ignore[arg-type]
    assert a.metrics == (Metric.MIN_VALUE,) and a.column == C
    hash(a)
    with pytest.raises(ValueError, match="table"):
        ColumnAggregate(T, col(U, "id"), (Metric.MIN_VALUE,))  # type: ignore[arg-type]


def test_composite_validation() -> None:
    with pytest.raises(ValueError, match="column"):
        CompositeKeyAggregate(T, (), (Metric.NON_NULL,))
    with pytest.raises(ValueError, match="metric"):
        CompositeKeyAggregate(T, (C,), ())
    with pytest.raises(ValueError, match="duplicate"):
        CompositeKeyAggregate(T, (C, C), (Metric.NON_NULL,))
    with pytest.raises(ValueError, match="tuple"):
        CompositeKeyAggregate(T, (C,), (Metric.SUM,))


def test_value_membership_validation() -> None:
    ValueMembership(T, C, ())  # empty declared: exact top-k (Amendment A R2)
    ValueMembership(T, C, ("a",), report_limit=1)
    ValueMembership(T, C, ("a",), report_limit=1000)
    for bad in (0, 1001, -1):
        with pytest.raises(ValueError, match="report_limit"):
            ValueMembership(T, C, ("a",), report_limit=bad)
    with pytest.raises(TypeError):
        ValueMembership(T, C, ("a",), report_limit=True)
    ValueMembership(T, C, ("a",), split=":")
    ValueMembership(T, C, ("a",), split=" - ")
    for bad_split in ("", "abcd", "x", "::::"):
        with pytest.raises(ValueError, match="separator"):
            ValueMembership(T, C, ("a",), split=bad_split)
    with pytest.raises(TypeError):
        ValueMembership(T, C, (1,))  # type: ignore[arg-type]


def test_key_overlap_validation() -> None:
    l2 = (col(T, "cid"), col(T, "shop_id"))
    r2 = (col(U, "id"), col(U, "shop_id"))
    KeyOverlap(l2, r2, right_filter=flt())
    with pytest.raises(ValueError, match="empty"):
        KeyOverlap((), ())
    with pytest.raises(ValueError, match="length"):
        KeyOverlap(l2, r2[:1])
    with pytest.raises(ValueError, match="one table"):
        KeyOverlap((col(T, "cid"), col(U, "x")), r2)
    with pytest.raises(ValueError, match="one table"):
        KeyOverlap(l2, (col(U, "id"), col(T, "x")))
    with pytest.raises(ValueError, match="cast"):
        KeyOverlap(l2, r2, cast_to="varchar")
    KeyOverlap(l2, r2, cast_to="string")
    # a bare ColumnRef is accepted as a one-part key
    k = KeyOverlap(col(T, "cid"), col(U, "id"))  # type: ignore[arg-type]
    assert k.left == (col(T, "cid"),)


def test_pattern_validation() -> None:
    PatternCoverage(T, C, "%@%.%")
    for bad in ("", "%", "%%", "_", "%_%"):
        with pytest.raises(ValueError, match="wildcard"):
            PatternCoverage(T, C, bad)


def test_metadata_query_has_no_sample() -> None:
    assert not hasattr(MetadataQuery(T), "sample")
    with pytest.raises(TypeError):
        MetadataQuery(T, sample=SampleSpec("system", Decimal("0.5")))  # type: ignore[call-arg]


# --- SampleSpec (Amendment B) ---------------------------------------------------------


def test_sample_spec_validation() -> None:
    SampleSpec("system", Decimal("0.01"))
    SampleSpec("bernoulli", Decimal(1), seed=7)
    SampleSpec("keyhash", Decimal("0.5"), key=(C,))
    for frac in (Decimal(0), Decimal("-0.1"), Decimal("1.01"), Decimal("NaN")):
        with pytest.raises(ValueError, match="fraction"):
            SampleSpec("system", frac)
    with pytest.raises(TypeError):
        SampleSpec("system", 0.5)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="key"):
        SampleSpec("keyhash", Decimal("0.5"))
    with pytest.raises(ValueError, match="key"):
        SampleSpec("system", Decimal("0.5"), key=(C,))
    with pytest.raises(ValueError, match="method"):
        SampleSpec("limit", Decimal("0.5"))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        SampleSpec("system", Decimal("0.5"), seed=True)


def test_every_sampled_primitive_accepts_a_sample() -> None:
    s = SampleSpec("keyhash", Decimal("0.1"), seed=3, key=(C,))
    plans = [
        ColumnAggregate(T, C, (Metric.ROW_COUNT,), sample=s),
        CompositeKeyAggregate(T, (C,), (Metric.NON_NULL,), sample=s),
        ValueMembership(T, C, (), sample=s),
        KeyOverlap((col(T, "id"),), (col(U, "id"),), sample=s),
        KeyOverlap((col(T, "id"),), (col(U, "id"),), right_sample=s),
        PatternCoverage(T, C, "a%", sample=s),
    ]
    assert all(is_sampled(p) for p in plans)
    assert not is_sampled(ColumnAggregate(T, C, (Metric.ROW_COUNT,)))
    assert not is_sampled(MetadataQuery(T))


# --- canonical key / hashability ------------------------------------------------------


def test_plans_are_hashable_and_equal_by_value() -> None:
    a = ColumnAggregate(T, C, (Metric.ROW_COUNT,), filter=flt())
    b = ColumnAggregate(T, C, (Metric.ROW_COUNT,), filter=flt())
    assert a == b and hash(a) == hash(b)
    assert len({a, b}) == 1


def test_canonical_key_is_deterministic_json() -> None:
    a = ColumnAggregate(T, C, (Metric.ROW_COUNT,), filter=flt(1, "p0"))
    b = ColumnAggregate(T, C, (Metric.ROW_COUNT,), filter=flt(1, "other_name"))
    assert canonical_key(a) == canonical_key(b)  # Param names do not matter
    json.loads(canonical_key(a))
    assert canonical_key(a) != canonical_key(ColumnAggregate(T, C, (Metric.NON_NULL,)))
    assert canonical_key(a) != canonical_key(
        ColumnAggregate(T, C, (Metric.ROW_COUNT,), filter=flt(2))
    )


def test_canonical_key_distinguishes_every_field() -> None:
    base = KeyOverlap((col(T, "id"),), (col(U, "id"),))
    variants = [
        KeyOverlap((col(T, "id"),), (col(U, "id"),), cast_to="string"),
        KeyOverlap((col(T, "id"),), (col(U, "id"),), measure_multiplicity=True),
        KeyOverlap((col(T, "id"),), (col(U, "id"),), filter=flt()),
        KeyOverlap((col(T, "id"),), (col(U, "id"),), right_filter=flt()),
        KeyOverlap((col(U, "id"),), (col(T, "id"),)),
        KeyOverlap((col(T, "ID"),), (col(U, "id"),)),
    ]
    keys = {canonical_key(v) for v in [base, *variants]}
    assert len(keys) == len(variants) + 1


def test_canonical_key_includes_sample() -> None:
    s1 = SampleSpec("system", Decimal("0.1"))
    s2 = SampleSpec("system", Decimal("0.1"), seed=1)
    exact = ColumnAggregate(T, C, (Metric.ROW_COUNT,))
    k = {
        canonical_key(exact),
        canonical_key(ColumnAggregate(T, C, (Metric.ROW_COUNT,), sample=s1)),
        canonical_key(ColumnAggregate(T, C, (Metric.ROW_COUNT,), sample=s2)),
    }
    assert len(k) == 3
    assert canonical_key(
        ColumnAggregate(T, C, (Metric.ROW_COUNT,), sample=SampleSpec("system", Decimal("0.10")))
    ) == canonical_key(ColumnAggregate(T, C, (Metric.ROW_COUNT,), sample=s1))
    # the same fields on different primitive types never collide
    assert canonical_key(PatternCoverage(T, C, "a%")) != canonical_key(
        ValueMembership(T, C, ("a%",))
    )


# --- registration ---------------------------------------------------------------------


def test_register_primitive() -> None:
    @dataclass(frozen=True)
    class Median:
        arity: ClassVar[Arity] = Arity.SINGLE_ROW
        table: TableRef
        column: Ident

        def result_columns(self) -> tuple[str, ...]:
            return ("median",)

    def lower(plan: object) -> object:
        return plan

    register_primitive(Median, lower=lower)
    try:
        assert ir.PRIMITIVE_LOWERINGS[Median] is lower
        with pytest.raises(ValueError, match="already"):
            register_primitive(Median, lower=lower)
        register_primitive(Median, lower=lower, replace=True)
        json.loads(canonical_key(Median(T, C)))
    finally:
        unregister_primitive(Median)
    assert Median not in PRIMITIVE_LOWERINGS

    class NotADataclass:
        arity = Arity.SINGLE_ROW

        def result_columns(self) -> tuple[str, ...]:
            return ()

    with pytest.raises(TypeError):
        register_primitive(NotADataclass, lower=lower)

    @dataclass(frozen=True)
    class NoArity:
        x: int

        def result_columns(self) -> tuple[str, ...]:
            return ()

    with pytest.raises(TypeError, match="arity"):
        register_primitive(NoArity, lower=lower)


def test_every_primitive_checks_table_filter_and_sample_kinds() -> None:
    bad_filter = "status = 1"
    builders = [
        lambda t, f: ColumnAggregate(t, C, (Metric.ROW_COUNT,), filter=f),
        lambda t, f: CompositeKeyAggregate(t, (C,), (Metric.NON_NULL,), filter=f),
        lambda t, f: ValueMembership(t, C, (), filter=f),
        lambda t, f: PatternCoverage(t, C, "a%", filter=f),
    ]
    for build in builders:
        build(T, flt())
        build(T, None)
        with pytest.raises(TypeError, match="table"):
            build("orders", None)
        with pytest.raises(TypeError, match="filter"):
            build(T, bad_filter)
    with pytest.raises(TypeError, match="table"):
        MetadataQuery("orders")  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="sample"):
        ColumnAggregate(T, C, (Metric.ROW_COUNT,), sample="10%")  # type: ignore[arg-type]
    k = (col(T, "cid"),), (col(U, "id"),)
    with pytest.raises(TypeError, match="filter"):
        KeyOverlap(*k, filter=bad_filter)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="right_filter"):
        KeyOverlap(*k, right_filter=bad_filter)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="right_sample"):
        KeyOverlap(*k, right_sample=0.1)  # type: ignore[arg-type]


def test_key_overlap_rejects_a_duplicate_column_within_a_side() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        KeyOverlap((col(T, "a"), col(T, "a")), (col(U, "x"), col(U, "y")))
    with pytest.raises(ValueError, match="duplicate"):
        KeyOverlap((col(T, "a"), col(T, "b")), (col(U, "x"), col(U, "x")))
    # the same column name on the two sides is fine
    KeyOverlap((col(T, "id"),), (col(U, "id"),))


def test_sample_fraction_whose_keyhash_threshold_rounds_to_zero_is_refused() -> None:
    for method, key in (("keyhash", (C,)), ("system", ()), ("bernoulli", ())):
        with pytest.raises(ValueError, match="below 5e-7"):
            SampleSpec(method, Decimal("0.0000004999"), key=key)  # type: ignore[arg-type]
    # 5e-7 rounds half-up to one bucket of 1_000_000: the smallest fraction accepted
    SampleSpec("keyhash", Decimal("0.0000005"), key=(C,))
    SampleSpec("system", Decimal("0.0000005"))


def test_unregister_primitive() -> None:
    @dataclass(frozen=True)
    class Mode:
        arity: ClassVar[Arity] = Arity.SINGLE_ROW
        table: TableRef

        def result_columns(self) -> tuple[str, ...]:
            return ("mode",)

    with pytest.raises(KeyError, match="Mode"):
        unregister_primitive(Mode)  # never registered
    register_primitive(Mode, lower=lambda plan: plan)
    unregister_primitive(Mode)
    assert Mode not in PRIMITIVE_LOWERINGS
    with pytest.raises(KeyError):
        unregister_primitive(Mode)  # already removed
    # a removed class can be registered again without replace=True
    register_primitive(Mode, lower=lambda plan: plan)
    unregister_primitive(Mode)
    assert "unregister_primitive" in ir.__all__
