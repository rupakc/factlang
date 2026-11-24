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


def widened_aggregates(count_fn: str = "COUNT") -> Override:
    """``Agg`` printer for engines whose ``AVG`` keeps an integer input's type.

    ``AVG(x + 0.0)``: a native INT AVG silently truncates (it divides as integers), and the
    input's type is not known at render time, so the widening is type-agnostic and additive
    (adding zero changes no value; NULL stays NULL). An INT becomes DECIMAL; DECIMAL/FLOAT
    inputs keep their kind. ``COUNT`` is spelled ``count_fn`` (T-SQL ``COUNT_BIG``).

    Trade-off (T-SQL ``e1 + e2``: scale ``max(s1, s2)``, precision ``max(s1, s2) +
    max(p1 - s1, p2 - s2) + 1``, scale cut by any overflow above 38 but never below
    ``min(s, 6)``): ``x + 0.0`` (DECIMAL(1,1)) is exact for every input except a
    DECIMAL(38,s) with ``s > 0``, which loses one fractional digit before AVG's own result
    type applies. Accepted: a silently truncated INT average is the larger error.

    ``SUM`` is deliberately left native (correctness over coverage, spec G7): every
    type-agnostic widening changes a digit of some DECIMAL, while a native INT overflow is a
    driver error (FL700 -> ERROR verdict), never a wrong number.
    """

    def agg(printer: Printer, x: A.Agg) -> str:
        arg = "*" if x.arg is None else printer.expr(x.arg)
        distinct = "DISTINCT " if x.distinct else ""
        if x.fn == "AVG":
            return f"AVG({distinct}{arg} + 0.0)"
        fn = count_fn if x.fn == "COUNT" else x.fn
        return f"{fn}({distinct}{arg})"

    return agg


__all__ = ["Override", "derived_union_subquery", "widened_aggregates"]
