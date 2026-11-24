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

    The input's type is not known at render time, so the widening is type-agnostic:
    ``AVG(x * 1.0)`` (an INT AVG would divide as integers; a DECIMAL/FLOAT input keeps its
    kind), ``SUM(x * <sum_widen>)`` when given (T-SQL ``CONVERT(BIGINT, 1)``: an INT sum
    becomes BIGINT instead of overflowing; DECIMAL/FLOAT inputs stay exact/float), and
    ``count_fn`` for COUNT (T-SQL ``COUNT_BIG``).
    """

    def agg(printer: Printer, x: A.Agg) -> str:
        arg = "*" if x.arg is None else printer.expr(x.arg)
        distinct = "DISTINCT " if x.distinct else ""
        if x.fn == "AVG":
            return f"AVG({distinct}{arg} * 1.0)"
        if x.fn == "SUM" and sum_widen is not None:
            return f"SUM({distinct}{arg} * {sum_widen})"
        fn = count_fn if x.fn == "COUNT" else x.fn
        return f"{fn}({distinct}{arg})"

    return agg


__all__ = ["Override", "derived_union_subquery", "widened_aggregates"]
