"""Fixtures for the integration tests; the shop itself is built by ``shop.make_shop``."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from shop import make_shop


@pytest.fixture(scope="module")
def duck() -> Iterator[Any]:
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    make_shop(con)
    yield con
    con.close()
