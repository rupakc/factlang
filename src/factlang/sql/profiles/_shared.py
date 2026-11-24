"""Print functions several native profiles share through ``DialectProfile.overrides``.

An override receives the :class:`~factlang.sql.printer.Printer` and the node, and returns SQL
text. Every function here only re-arranges text the printer itself produced (identifiers,
parameters, sub-expressions), so the printer's safety rules (spec 8.4) still hold.
"""

from __future__ import annotations

from collections.abc import Callable

from factlang.sql import ast as A
from factlang.sql.printer import Printer

Override = Callable[..., str]


def derived_union_subquery(printer: Printer, x: A.SubqueryFrom) -> str:
    """A ``SubqueryFrom``; a UNION ALL branch with ORDER BY/LIMIT becomes a derived table.

    ``(SELECT … ORDER BY … LIMIT n) UNION ALL …`` is not accepted by every engine; the
    derived-table form ``SELECT * FROM (SELECT … LIMIT n) fl_dN`` is.
    """
    if isinstance(x.select, A.UnionAll):
        branches = []
        for s in x.select.selects:
            text = printer.select(s)
            if s.order_by or s.limit is not None:
                text = f"SELECT * FROM ({text}) {printer.derived_alias()}"
            branches.append(text)
        inner = " UNION ALL ".join(branches)
    else:
        inner = printer.select(x.select)
    return f"({inner}) {printer.alias(x.alias)}"


def widened_aggregates(count_fn: str = "COUNT", sum_widen: str | None = None) -> Override:
    """``Agg`` printer for engines whose aggregates keep a narrow integer input type.

    The input's type is not known at render time, so the widening is type-agnostic and
    additive (adding a zero changes no value and NULL stays NULL):

    - ``AVG(x + 0.0)``: an INT input becomes DECIMAL, so AVG no longer divides as integers;
      DECIMAL/FLOAT inputs keep their kind.
    - ``SUM(x + <sum_widen>)`` when given (T-SQL ``CONVERT(BIGINT, 0)``): an INT sum becomes
      BIGINT instead of overflowing past 2^31; FLOAT/MONEY are unchanged.

    Precision rule (T-SQL ``e1 + e2``): scale ``max(s1, s2)``, precision
    ``max(s1, s2) + max(p1 - s1, p2 - s2) + 1``; above 38 the scale is cut by the overflow,
    but never below ``min(s, 6)``. With BIGINT = DECIMAL(19,0), ``DECIMAL(p,s) + BIGINT`` keeps
    every digit when ``s <= 18`` and ``p <= 37`` (``x * CONVERT(BIGINT, 1)`` would add 19
    digits of precision and round already at ``p >= 19``). Residual: a DECIMAL with
    ``s > 18`` and ``p - s < 19`` loses ``s - 18`` fractional digits, and a DECIMAL(38,s)
    with ``s > 0`` loses one. ``x + 0.0`` (DECIMAL(1,1)) adds one fractional digit, so only a
    DECIMAL(38,s) loses one before AVG's own result type applies.
    """

    def agg(printer: Printer, x: A.Agg) -> str:
        arg = "*" if x.arg is None else printer.expr(x.arg)
        distinct = "DISTINCT " if x.distinct else ""
        if x.fn == "AVG":
            return f"AVG({distinct}{arg} + 0.0)"
        if x.fn == "SUM" and sum_widen is not None:
            return f"SUM({distinct}{arg} + {sum_widen})"
        fn = count_fn if x.fn == "COUNT" else x.fn
        return f"{fn}({distinct}{arg})"

    return agg


__all__ = ["Override", "derived_union_subquery", "widened_aggregates"]
