"""Print functions several native profiles share through ``DialectProfile.overrides``.

An override receives the :class:`~factlang.sql.printer.Printer` and the node, and returns SQL
text. Every function here only re-arranges text the printer itself produced (identifiers,
parameters, sub-expressions), so the printer's safety rules (spec 8.4) still hold.
"""

from __future__ import annotations

from collections.abc import Callable

from factlang.semantics.refs import Ident
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
                text = f"SELECT * FROM ({text}) {printer._derived_alias()}"
            branches.append(text)
        inner = " UNION ALL ".join(branches)
    else:
        inner = printer.select(x.select)
    return f"({inner}) {printer.alias(x.alias)}"


def backslash_quoting(quote_escape: str) -> tuple[Override, Override]:
    """Column and TableName printers for engines where ``\\`` escapes in a quoted name.

    In BigQuery and ClickHouse a backslash inside a quoted identifier starts an escape
    sequence, so the printer's plain quote-doubling would let ``a\\nb`` name a different
    object. Here a quoted name has every backslash doubled and every quote character
    written as ``quote_escape`` (BigQuery ``\\```, ClickHouse ``""``): the warehouse reads
    back exactly the claim's text (spec 8.4).
    """

    def ident(printer: Printer, i: Ident) -> str:
        p = printer.profile
        if not i.quoted:
            return printer.ident(i)
        printer.check_ident(i.text, True)
        q = p.quote_char
        return q + i.text.replace("\\", "\\\\").replace(q, quote_escape) + q

    def column(printer: Printer, x: A.Column) -> str:
        col = ident(printer, x.name)
        return f"{printer.alias(x.qualifier)}.{col}" if x.qualifier else col

    def table_name(printer: Printer, x: A.TableName) -> str:
        parts = x.ref.parts()
        if len(parts) > printer.profile.max_table_parts:
            printer.table(x.ref)  # raises FL505
        out = ".".join(ident(printer, part) for part in parts)
        if x.alias is not None:
            out += " " + printer.alias(x.alias)
        if x.sample is not None:
            out += " " + printer.table_sample(x.sample)
        return out

    return column, table_name


def avg_as_decimal(count_fn: str = "COUNT") -> Override:
    """``Agg`` printer: AVG over ``x * 1.0`` (an integer AVG would truncate), COUNT as given."""

    def agg(printer: Printer, x: A.Agg) -> str:
        arg = "*" if x.arg is None else printer.expr(x.arg)
        distinct = "DISTINCT " if x.distinct else ""
        if x.fn == "AVG":
            return f"AVG({distinct}{arg} * 1.0)"
        fn = count_fn if x.fn == "COUNT" else x.fn
        return f"{fn}({distinct}{arg})"

    return agg


__all__ = ["Override", "avg_as_decimal", "backslash_quoting", "derived_union_subquery"]
