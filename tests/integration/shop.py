"""The shop fixture warehouse with known answers (Task 19; reused by the Task 20 matrix).

``make_shop(conn, style=...)`` builds the shop tables on any PEP 249 connection. The schema is
written once with neutral column types (:data:`TABLES`) and spelled per dialect by a
:class:`~engines.Style` (``engines.py``), so every engine holds the same rows and every number
the expectations in ``expectations.py`` name follows from the rows written here.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from engines import DUCKDB_STYLE, Style

# The newest ``events.created_at`` is this long before the moment the fixture is built.
EVENTS_AGE = dt.timedelta(hours=1)
ORDERS = 100
CUSTOMERS = 10
PARENT_KEYS = 1000
BIG = 2_000_000_000  # two of these overflow a 32-bit INTEGER sum

# table -> ((column, neutral type), ...); see engines.Style.column_type
TABLES: dict[str, tuple[tuple[str, str], ...]] = {
    "customers": (("id", "INT"), ("name", "STR(40)"), ("email", "STR(80)")),
    "orders": (
        ("id", "INT"),
        ("customer_id", "INT"),
        ("status", "STR(40)"),
        ("amount", "DEC(10,2)"),
        ("created_at", "TS"),
        ("shipped_at", "TS"),
    ),
    "order_items": (("order_id", "INT"), ("line", "INT"), ("price", "DEC(10,2)")),
    "events": (("id", "INT"), ("created_at", "TS")),
    "empty_t": (("id", "INT"), ("v", "INT"), ("s", "STR(40)"), ("ts", "TS")),
    "all_null_t": (("id", "INT"), ("v", "INT"), ("s", "STR(40)"), ("ts", "TS")),
    "Order Items": (("order", "INT"), ("qty", "INT")),
    "refs_parent": (("k", "INT"),),
    "refs_child": (("id", "INT"), ("k", "INT")),
    "comp_left": (("a", "INT"), ("b", "STR(10)")),
    "comp_right": (("a", "INT"), ("b", "STR(10)")),
    # Task 20 engine checks: a column literally named ``value`` (a ValueMembership alias), a
    # NULL among split/membership values, a boolean for bare filter atoms (BIT on T-SQL)
    "tags": (("id", "INT"), ("label", "STR(20)"), ("value", "INT"), ("deleted", "BOOL")),
    # NOT NULL columns, no rows: ClickHouse aggregates of no rows over a non-Nullable column
    # give 0 / nan unless spelled -OrNull; they must stay NULL (NO_DATA), never a measured 0
    "empty_nn": (("id", "INT!"), ("v", "INT!"), ("d", "DEC(10,2)!")),
    # an INTEGER column whose SUM overflows 32 bits (T-SQL: loud ERROR, never a wrong number)
    "big_t": (("id", "INT"), ("v", "INT")),
}

TAGS_ROWS: tuple[tuple[object, ...], ...] = (
    (1, "a", 1, False),
    (2, "a", 2, False),
    (3, "b", 3, True),
    (4, None, 4, True),
    (5, "c: x", None, False),
    (6, "c", 6, None),
)


def _insert(cur: Any, style: Style, table: str, rows: Sequence[Sequence[object]]) -> None:
    chunk = 500  # T-SQL takes at most 1000 rows per VALUES list
    for start in range(0, len(rows), chunk):
        values = ", ".join(
            "(" + ", ".join(style.literal(v) for v in row) + ")"
            for row in rows[start : start + chunk]
        )
        cur.execute(f"INSERT INTO {style.quote(table)} VALUES {values}")
        if style.drain:
            cur.fetchall()


def _run(cur: Any, style: Style, sql: str) -> None:
    cur.execute(sql)
    if style.drain:
        cur.fetchall()


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
    style: Style = DUCKDB_STYLE,
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
    - ``tags`` 6 rows (:data:`TAGS_ROWS`): ``label`` with a NULL and a ``'c: x'``; a column
      named ``value`` (sum 16 over 5 non-NULL); ``deleted`` TRUE x2, FALSE x3, NULL x1.
    - ``empty_nn`` 0 rows, every column NOT NULL (non-``Nullable`` on ClickHouse).
    - ``big_t`` 2 rows with ``v`` = 2,000,000,000 (sum past 2^31).

    Tables that already exist are dropped first, so a long-lived local engine can be reused.
    """
    if now is None:
        now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None, microsecond=0)
    cur = conn.cursor()
    for table, columns in TABLES.items():
        _run(cur, style, style.drop_sql.format(style.quote(table)))
        _run(cur, style, style.create(table, columns))
    _insert(
        cur,
        style,
        "customers",
        [
            (i, f"c{i}", "broken" if i == CUSTOMERS else f"c{i}@shop.test")
            for i in range(1, CUSTOMERS + 1)
        ],
    )
    base = dt.datetime(2026, 1, 1)
    _insert(
        cur,
        style,
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
        style,
        "order_items",
        [
            (i, line, Decimal("1.10") * line)
            for i in range(1, ORDERS + 1)
            for line in range(1, items_per_order(i) + 1)
        ],
    )
    _insert(
        cur,
        style,
        "events",
        [(i, now - EVENTS_AGE - dt.timedelta(hours=20 - i)) for i in range(1, 21)],
    )
    _insert(cur, style, "all_null_t", [(i, None, None, None) for i in range(1, 11)])
    _insert(cur, style, "Order Items", [(1, 2), (2, 1), (3, 5)])
    _insert(cur, style, "refs_parent", [(k,) for k in range(1, PARENT_KEYS + 1)])
    _insert(cur, style, "refs_child", [(1, 1), (2, 1), (3, 2), (4, 3), (5, 3)])
    _insert(cur, style, "comp_left", [(1, "x"), (1, "x"), (2, "x"), (2, "y"), (3, None)])
    _insert(cur, style, "comp_right", [(1, "x"), (2, "x"), (4, "z")])
    _insert(cur, style, "tags", TAGS_ROWS)
    _insert(cur, style, "big_t", [(1, BIG), (2, BIG)])
    close = getattr(cur, "close", None)
    if close is not None:
        close()
    commit = getattr(conn, "commit", None)
    if commit is not None:
        commit()
    return now
