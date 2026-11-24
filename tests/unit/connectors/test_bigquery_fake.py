"""BigQueryConnector with a fake ``google.cloud.bigquery`` module (spec 9.1, 18 R5)."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import sys
import types
from decimal import Decimal
from typing import Any

import pytest

from factlang.connectors import BigQueryConnector, ExecutionError, dry_run_bytes
from factlang.ir import ColumnAggregate, Metric
from factlang.semantics.filters import FAnd, FCompare, Param
from factlang.semantics.refs import Ident, TableRef
from factlang.sql import RenderedProbe, get_profile, render

BQ = get_profile("bigquery")
assert BQ is not None


class ScalarQueryParameter:
    def __init__(self, name: str, type_: str, value: Any) -> None:
        self.name, self.type_, self.value = name, type_, value

    def __repr__(self) -> str:
        return f"Scalar({self.name}, {self.type_}, {self.value!r})"


class ArrayQueryParameter:
    def __init__(self, name: str, array_type: str, values: list[Any]) -> None:
        self.name, self.array_type, self.values = name, array_type, values


class QueryJobConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.dry_run = False
        self.use_query_cache = True
        self.query_parameters: list[Any] = []
        self.default_dataset: Any = None
        self.maximum_bytes_billed: Any = None
        for k, v in kwargs.items():
            setattr(self, k, v)


class Row(dict[str, Any]):
    pass


class FakeJob:
    def __init__(self, client: FakeClient, sql: str, config: QueryJobConfig) -> None:
        self.client, self.sql, self.config = client, sql, config
        self.total_bytes_processed = 1234
        self.cancelled = False

    def result(self, timeout: float | None = None) -> list[Row]:
        self.client.result_timeouts.append(timeout)
        if self.client.raise_exc is not None:
            raise self.client.raise_exc
        return [Row(row_count=7)]

    def cancel(self) -> None:
        self.cancelled = True


class FakeClient:
    def __init__(self) -> None:
        self.project = "proj"
        self.jobs: list[FakeJob] = []
        self.result_timeouts: list[float | None] = []
        self.raise_exc: BaseException | None = None
        self.query_exc: BaseException | None = None

    def query(self, sql: str, job_config: QueryJobConfig, **kw: Any) -> FakeJob:
        if self.query_exc is not None:
            raise self.query_exc
        job = FakeJob(self, sql, job_config)
        self.jobs.append(job)
        return job


@pytest.fixture(autouse=True)
def fake_bigquery(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    mod = types.ModuleType("google.cloud.bigquery")
    mod.ScalarQueryParameter = ScalarQueryParameter  # type: ignore[attr-defined]
    mod.ArrayQueryParameter = ArrayQueryParameter  # type: ignore[attr-defined]
    mod.QueryJobConfig = QueryJobConfig  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", mod)
    return mod


ORDERS = TableRef(None, Ident("shop"), Ident("orders"))


def probe() -> RenderedProbe:
    flt = FAnd(
        (
            FCompare(Ident("status"), "=", Param("p1", "paid", "string")),
            FCompare(Ident("n"), ">", Param("p2", 3, "integer")),
            FCompare(Ident("total"), ">=", Param("p3", Decimal("1.5"), "decimal")),
            FCompare(Ident("ok"), "=", Param("p4", True, "boolean")),
            FCompare(Ident("d"), ">=", Param("p5", dt.date(2026, 1, 1), "date")),
            FCompare(
                Ident("ts"),
                "<",
                Param("p6", dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc), "timestamp"),
            ),
            FCompare(Ident("r"), "<", Param("p7", 0.5, "float")),
        )
    )
    return render(ColumnAggregate(ORDERS, None, (Metric.ROW_COUNT,), filter=flt), BQ)


def test_typed_scalar_parameters() -> None:
    client = FakeClient()
    c = BigQueryConnector(client)
    assert c.dialect == "bigquery" and c.paramstyle == "bigquery"
    result = c.execute(probe(), timeout_s=None)
    assert result.rows == [{"row_count": 7}] and type(result.rows[0]) is dict
    assert result.bytes_processed == 1234
    job = client.jobs[0]
    assert "@p1" in job.sql and "paid" not in job.sql
    types_ = {p.name: (p.type_, p.value) for p in job.config.query_parameters}
    assert types_ == {
        "p1": ("STRING", "paid"),
        "p2": ("INT64", 3),
        "p3": ("NUMERIC", Decimal("1.5")),
        "p4": ("BOOL", True),
        "p5": ("DATE", dt.date(2026, 1, 1)),
        "p6": ("TIMESTAMP", dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)),
        "p7": ("FLOAT64", 0.5),
    }
    assert job.config.dry_run is False


def test_array_parameter_for_a_sequence_value() -> None:
    client = FakeClient()
    p = RenderedProbe(
        "SELECT 1 AS row_count FROM t WHERE x IN UNNEST({{a}})",
        (Param("a", ("x", "y"), "string"),),
        probe().arity,
        probe().plan,
        "bigquery",
        "native",
    )
    BigQueryConnector(client).execute(p, timeout_s=None)
    (param,) = client.jobs[0].config.query_parameters
    assert isinstance(param, ArrayQueryParameter)
    assert (param.name, param.array_type, param.values) == ("a", "STRING", ["x", "y"])


def test_defaults_and_budget_in_job_config() -> None:
    client = FakeClient()
    BigQueryConnector(
        client, default_project="p2", default_dataset="shop", maximum_bytes_billed=10**9
    ).execute(probe(), timeout_s=12.5)
    cfg = client.jobs[0].config
    assert cfg.default_dataset == "p2.shop"
    assert cfg.maximum_bytes_billed == 10**9
    assert client.result_timeouts == [12.5]


def test_default_dataset_uses_client_project() -> None:
    client = FakeClient()
    BigQueryConnector(client, default_dataset="shop").execute(probe(), timeout_s=None)
    assert client.jobs[0].config.default_dataset == "proj.shop"


def test_dry_run_bytes() -> None:
    client = FakeClient()
    c = BigQueryConnector(client, maximum_bytes_billed=5)
    assert c.dry_run_bytes(probe()) == 1234
    cfg = client.jobs[0].config
    assert cfg.dry_run is True and cfg.use_query_cache is False
    assert len(cfg.query_parameters) == 7
    assert client.result_timeouts == []  # a dry run is never waited on as a query


def test_dry_run_helper_on_any_connector() -> None:
    assert dry_run_bytes(BigQueryConnector(FakeClient()), probe()) == 1234

    class NoDryRun:
        dialect = "duckdb"
        paramstyle = "qmark"

    assert dry_run_bytes(NoDryRun(), probe()) is None  # type: ignore[arg-type]


def test_dry_run_errors_are_fl700() -> None:
    client = FakeClient()
    client.query_exc = RuntimeError("Not found: Table proj:shop.orders")
    with pytest.raises(ExecutionError) as info:
        BigQueryConnector(client).dry_run_bytes(probe())
    assert info.value.code == "FL700"


def test_timeout_is_fl702_and_cancels_the_job() -> None:
    client = FakeClient()
    client.raise_exc = concurrent.futures.TimeoutError()
    with pytest.raises(ExecutionError) as info:
        BigQueryConnector(client).execute(probe(), timeout_s=1)
    assert info.value.code == "FL702"
    assert client.jobs[0].cancelled


def test_bytes_billed_limit_is_fl701() -> None:
    client = FakeClient()
    client.raise_exc = RuntimeError(
        "400 Query exceeded limit for bytes billed: 10. 20971520 or higher required. "
        "reason: bytesBilledLimitExceeded"
    )
    with pytest.raises(ExecutionError) as info:
        BigQueryConnector(client, maximum_bytes_billed=10).execute(probe(), timeout_s=None)
    assert info.value.code == "FL701"


def test_other_errors_are_fl700() -> None:
    client = FakeClient()
    client.raise_exc = RuntimeError("Syntax error")
    with pytest.raises(ExecutionError) as info:
        BigQueryConnector(client).execute(probe(), timeout_s=None)
    assert info.value.code == "FL700"
    assert isinstance(info.value.__cause__, RuntimeError)


def test_missing_library_names_the_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", None)
    with pytest.raises(ImportError, match=r"factlang\[bigquery\]"):
        BigQueryConnector(FakeClient()).execute(probe(), timeout_s=None)
