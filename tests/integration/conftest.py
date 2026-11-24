"""Fixture warehouse with known answers (Task 19; reused by the Task 20 engine matrix).

``make_shop(conn)`` builds the shop tables on any PEP 249 connection with portable SQL
(``INTEGER``, ``VARCHAR``, ``DECIMAL``, ``TIMESTAMP`` literals). Every number the expectations
in ``expectations.py`` name follows from the rows written here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable, Iterator, Sequence
from decimal import Decimal
from typing import Any

import pytest

# The newest ``events.created_at`` is this long before the moment the fixture is built.
EVENTS_AGE = dt.timedelta(hours=1)
ORDERS = 100
CUSTOMERS = 10
PARENT_KEYS = 1000


def _lit(value: object) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, Decimal)):
        return str(value)
    if isinstance(value, dt.datetime):
        return "TIMESTAMP '" + value.strftime("%Y-%m-%d %H:%M:%S") + "'"
    return "'" + str(value).replace("'", "''") + "'"


def _insert(cur: Any, table: str, rows: Sequence[Sequence[object]], chunk: int = 500) -> None:
    for start in range(0, len(rows), chunk):
        values = ", ".join(
            "(" + ", ".join(_lit(v) for v in row) + ")" for row in rows[start : start + chunk]
        )
        cur.execute(f"INSERT INTO {table} VALUES {values}")


def order_status(i: int) -> str:
    if i in (10, 20, 30):
        return "other: late"
    return ("open", "closed", "churned")[i % 3]


def order_customer(i: int) -> int:
    return {99: 11, 100: 12}.get(i, (i - 1) % CUSTOMERS + 1)


def items_per_order(i: int) -> int:
    return i % 3 + 1  # 1, 2 or 3 lines


def make_shop(
    conn: Any,
    *,
    quote: Callable[[str], str] = lambda s: '"' + s + '"',
    now: dt.datetime | None = None,
) -> dt.datetime:
    """Create and fill the shop tables; returns the naive-UTC ``now`` the events are aged from.

    - ``customers`` 10 rows, unique ``id`` 1..10; ``email`` valid for 9 of them.
    - ``orders`` 100 rows: ``amount`` = id + 0.25; 5 NULL ``shipped_at`` (ids 20, 40, ...);
      ``customer_id`` 98 matching (ids 1..10) + 2 orphans (11, 12); ``status`` in
      open/closed/churned plus 3 rows ``'other: late'``.
    - ``order_items`` 1-3 lines per order (fan-out 3 max, 200 rows), ``price`` = 1.10 x line.
    - ``events`` 20 rows, newest ``created_at`` = now - 1h.
    - ``empty_t`` 0 rows; ``all_null_t`` 10 rows with ``v``/``s``/``ts`` all NULL.
    - ``"Order Items"`` 3 rows, with a column named ``order``.
    - ``refs_parent`` keys 1..1000; ``refs_child`` 5 rows referencing 3 of them.
    - ``comp_left``/``comp_right``: a 2-part key pair; one left row has a NULL part.
    """
    if now is None:
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    q = quote
    cur = conn.cursor()
    ddl = [
        "CREATE TABLE customers (id INTEGER, name VARCHAR(40), email VARCHAR(80))",
        "CREATE TABLE orders (id INTEGER, customer_id INTEGER, status VARCHAR(40), "
        "amount DECIMAL(10,2), created_at TIMESTAMP, shipped_at TIMESTAMP)",
        "CREATE TABLE order_items (order_id INTEGER, line INTEGER, price DECIMAL(10,2))",
        "CREATE TABLE events (id INTEGER, created_at TIMESTAMP)",
        "CREATE TABLE empty_t (id INTEGER, v INTEGER, s VARCHAR(40), ts TIMESTAMP)",
        "CREATE TABLE all_null_t (id INTEGER, v INTEGER, s VARCHAR(40), ts TIMESTAMP)",
        f"CREATE TABLE {q('Order Items')} ({q('order')} INTEGER, qty INTEGER)",
        "CREATE TABLE refs_parent (k INTEGER)",
        "CREATE TABLE refs_child (id INTEGER, k INTEGER)",
        "CREATE TABLE comp_left (a INTEGER, b VARCHAR(10))",
        "CREATE TABLE comp_right (a INTEGER, b VARCHAR(10))",
    ]
    for stmt in ddl:
        cur.execute(stmt)
    _insert(
        cur,
        "customers",
        [
            (i, f"c{i}", "broken" if i == CUSTOMERS else f"c{i}@shop.test")
            for i in range(1, CUSTOMERS + 1)
        ],
    )
    base = dt.datetime(2026, 1, 1)
    _insert(
        cur,
        "orders",
        [
            (
                i,
                order_customer(i),
                order_status(i),
                Decimal(i) + Decimal("0.25"),
                base + dt.timedelta(days=i),
                None if i % 20 == 0 else base + dt.timedelta(days=i + 2),
            )
            for i in range(1, ORDERS + 1)
        ],
    )
    _insert(
        cur,
        "order_items",
        [
            (i, line, Decimal("1.10") * line)
            for i in range(1, ORDERS + 1)
            for line in range(1, items_per_order(i) + 1)
        ],
    )
    _insert(
        cur, "events", [(i, now - EVENTS_AGE - dt.timedelta(hours=20 - i)) for i in range(1, 21)]
    )
    _insert(cur, "all_null_t", [(i, None, None, None) for i in range(1, 11)])
    _insert(cur, q("Order Items"), [(1, 2), (2, 1), (3, 5)])
    _insert(cur, "refs_parent", [(k,) for k in range(1, PARENT_KEYS + 1)])
    _insert(cur, "refs_child", [(1, 1), (2, 1), (3, 2), (4, 3), (5, 3)])
    _insert(cur, "comp_left", [(1, "x"), (1, "x"), (2, "x"), (2, "y"), (3, None)])
    _insert(cur, "comp_right", [(1, "x"), (2, "x"), (4, "z")])
    close = getattr(cur, "close", None)
    if close is not None:
        close()
    commit = getattr(conn, "commit", None)
    if commit is not None:
        commit()
    return now


@pytest.fixture(scope="module")
def duck() -> Iterator[Any]:
    duckdb = pytest.importorskip("duckdb")
    con = duckdb.connect()
    make_shop(con)
    yield con
    con.close()
