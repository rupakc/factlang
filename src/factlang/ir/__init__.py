"""Dialect-free IR of measurement primitives (spec 8.1, layer 8)."""

from __future__ import annotations

from factlang.ir.metrics import ALIASES, KEY_METRICS, TABLE_METRICS, Arity, Metric
from factlang.ir.primitives import (
    CORE_PRIMITIVES,
    PRIMITIVE_LOWERINGS,
    REPORT_LIMIT_MAX,
    SAMPLE_METHODS,
    ColumnAggregate,
    CompositeKeyAggregate,
    KeyOverlap,
    MetadataQuery,
    PatternCoverage,
    ProbePlan,
    SampleMethod,
    SampleSpec,
    ValueMembership,
    canonical_key,
    is_sampled,
    register_primitive,
)
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef

__all__ = [
    "ALIASES",
    "CORE_PRIMITIVES",
    "KEY_METRICS",
    "PRIMITIVE_LOWERINGS",
    "REPORT_LIMIT_MAX",
    "SAMPLE_METHODS",
    "TABLE_METRICS",
    "Arity",
    "ColumnAggregate",
    "ColumnRef",
    "CompositeKeyAggregate",
    "EdgeRef",
    "Ident",
    "KeyOverlap",
    "MetadataQuery",
    "Metric",
    "PatternCoverage",
    "ProbePlan",
    "SampleMethod",
    "SampleSpec",
    "TableRef",
    "ValueMembership",
    "canonical_key",
    "is_sampled",
    "register_primitive",
]
