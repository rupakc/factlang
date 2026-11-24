"""Metrics, their wire-format aliases and primitive result arity (spec 8.1)."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType


class Metric(Enum):
    ROW_COUNT = "ROW_COUNT"
    NON_NULL = "NON_NULL"
    DISTINCT_KEYS = "DISTINCT_KEYS"
    MAX_PER_KEY = "MAX_PER_KEY"
    MIN_VALUE = "MIN_VALUE"
    MAX_VALUE = "MAX_VALUE"
    SUM = "SUM"
    AVG = "AVG"
    DUPLICATE_KEYS = "DUPLICATE_KEYS"
    NOW = "NOW"


# Result column aliases: a wire format, pinned by test. Never ``rows`` (reserved in BigQuery).
ALIASES: Mapping[Metric, str] = MappingProxyType(
    {
        Metric.ROW_COUNT: "row_count",
        Metric.NON_NULL: "non_null",
        Metric.DISTINCT_KEYS: "distinct_keys",
        Metric.MAX_PER_KEY: "max_rows_per_key",
        Metric.MIN_VALUE: "min_value",
        Metric.MAX_VALUE: "max_value",
        Metric.SUM: "sum_value",
        Metric.AVG: "avg_value",
        Metric.DUPLICATE_KEYS: "duplicate_keys",
        Metric.NOW: "server_now",
    }
)

# Metrics that need no column: they describe the table (or the server), not a value.
TABLE_METRICS: frozenset[Metric] = frozenset({Metric.ROW_COUNT, Metric.NOW})

# Metrics meaningful over a composite key (a tuple of columns, grouped, never concatenated).
KEY_METRICS: frozenset[Metric] = frozenset(
    {
        Metric.ROW_COUNT,
        Metric.NON_NULL,
        Metric.DISTINCT_KEYS,
        Metric.MAX_PER_KEY,
        Metric.DUPLICATE_KEYS,
    }
)


class Arity(Enum):
    """Shape of a primitive's result: the runner shapes rows from this, never ``rows[0]``."""

    SINGLE_ROW = "single_row"
    ROW_SET = "row_set"


__all__ = ["ALIASES", "KEY_METRICS", "TABLE_METRICS", "Arity", "Metric"]
