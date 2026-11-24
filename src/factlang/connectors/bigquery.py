"""BigQueryConnector (spec 9.1, 18 R5; layer 14, optional extra ``bigquery``).

``google-cloud-bigquery`` is imported lazily. Every parameter is a typed
``ScalarQueryParameter`` (``ArrayQueryParameter`` for a sequence value) whose type comes
from the ``bigquery`` profile's ``param_types``; ``dry_run_bytes`` prices a probe with a
dry-run job and never executes it.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import importlib
import re
from collections.abc import Mapping
from types import MappingProxyType, ModuleType
from typing import Any

from factlang.connectors.base import ExecutionError, QueryResult, map_error, normalise_rows
from factlang.semantics.filters import Param
from factlang.sql import RenderedProbe, get_profile

# used only if no ``bigquery`` profile is registered
FALLBACK_PARAM_TYPES: Mapping[str, str] = MappingProxyType(
    {
        "integer": "INT64",
        "decimal": "NUMERIC",
        "float": "FLOAT64",
        "string": "STRING",
        "boolean": "BOOL",
        "date": "DATE",
        "timestamp": "TIMESTAMP",
    }
)
_BYTES_LIMIT = re.compile(r"bytesBilledLimitExceeded|exceeded limit for bytes billed", re.I)


def _bigquery() -> ModuleType:
    try:
        return importlib.import_module("google.cloud.bigquery")
    except ImportError as exc:
        raise ImportError(
            "google-cloud-bigquery is required for this connector: pip install factlang[bigquery]"
        ) from exc


class BigQueryConnector:
    paramstyle = "bigquery"
    dialect = "bigquery"

    def __init__(
        self,
        client: Any,
        *,
        default_project: str | None = None,
        default_dataset: str | None = None,
        maximum_bytes_billed: int | None = None,
    ) -> None:
        self.client = client
        self.default_project = default_project
        self.default_dataset = default_dataset
        self.maximum_bytes_billed = maximum_bytes_billed

    def _param_types(self) -> Mapping[str, str]:
        profile = get_profile(self.dialect)
        return profile.param_types if profile is not None else FALLBACK_PARAM_TYPES

    def _parameter(self, bq: ModuleType, p: Param, types: Mapping[str, str]) -> Any:
        type_ = types.get(p.family)
        if type_ is None:  # pragma: no cover - every built-in family has a BigQuery type
            raise ExecutionError(f"no BigQuery type for parameter family {p.family}", code="FL700")
        if isinstance(p.value, (list, tuple)):
            return bq.ArrayQueryParameter(p.name, type_, list(p.value))
        return bq.ScalarQueryParameter(p.name, type_, p.value)

    def _config(self, bq: ModuleType, probe: RenderedProbe, *, dry_run: bool) -> tuple[str, Any]:
        sql, _ = probe.bind(self.paramstyle)
        types = self._param_types()
        config = bq.QueryJobConfig()
        config.query_parameters = [self._parameter(bq, p, types) for p in probe.params]
        if self.default_dataset is not None:
            project = self.default_project or getattr(self.client, "project", None)
            config.default_dataset = (
                f"{project}.{self.default_dataset}" if project else self.default_dataset
            )
        if self.maximum_bytes_billed is not None:
            config.maximum_bytes_billed = self.maximum_bytes_billed
        if dry_run:
            config.dry_run = True
            config.use_query_cache = False
        return sql, config

    def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult:
        bq = _bigquery()
        sql, config = self._config(bq, probe, dry_run=False)
        job = None
        try:
            job = self.client.query(sql, job_config=config)
            rows = [dict(row.items()) for row in job.result(timeout=timeout_s)]
        except Exception as exc:
            if isinstance(exc, ExecutionError):
                error = exc
            elif _BYTES_LIMIT.search(str(exc)):
                error = ExecutionError(
                    f"over maximum_bytes_billed ({type(exc).__name__}: {exc})", code="FL701"
                )
            else:
                timed_out = isinstance(exc, concurrent.futures.TimeoutError)
                error = map_error(exc, timed_out=timed_out, timeout_s=timeout_s)
            if error.code == "FL702" and job is not None:
                with contextlib.suppress(Exception):  # best effort: stop paying for it
                    job.cancel()
            if error is exc:
                raise
            raise error from exc
        processed = getattr(job, "total_bytes_processed", None)
        return QueryResult(
            normalise_rows(probe, rows), None if processed is None else int(processed)
        )

    def dry_run_bytes(self, probe: RenderedProbe) -> int | None:
        """Bytes BigQuery would process for ``probe`` (a dry-run job; nothing executes)."""
        bq = _bigquery()
        sql, config = self._config(bq, probe, dry_run=True)
        try:
            job = self.client.query(sql, job_config=config)
        except ExecutionError:
            raise
        except Exception as exc:
            raise map_error(exc) from exc
        processed = getattr(job, "total_bytes_processed", None)
        return None if processed is None else int(processed)


__all__ = ["FALLBACK_PARAM_TYPES", "BigQueryConnector"]
