"""Opt-in live tests for the ``native`` tier: bigquery, snowflake, redshift, databricks,
oracle (spec 8.5, 13). They need a cloud account and never run in CI.

A dialect runs only when ``FACTLANG_LIVE_<DIALECT>=1``; otherwise its tests are skipped.
Connection settings come from ``FACTLANG_LIVE_<DIALECT>_CONNECT``, a JSON object passed as
keyword arguments to the driver's ``connect`` (for BigQuery: to ``bigquery.Client``, plus
optional ``default_dataset``). ``FACTLANG_LIVE_<DIALECT>_TABLE`` names an existing table with
at least one row (qualified as the dialect needs) and ``FACTLANG_LIVE_<DIALECT>_COLUMN`` a
string column of it. Nothing is written to the warehouse.
"""

from __future__ import annotations

import pytest
from live_targets import LIVE_DIALECTS, LiveTarget, open_target


@pytest.fixture(scope="module", params=LIVE_DIALECTS)
def live(request: pytest.FixtureRequest) -> LiveTarget:
    return open_target(request.param)
