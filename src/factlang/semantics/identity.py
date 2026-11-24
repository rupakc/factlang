"""Semantic identity of a resolved claim (spec 4.6).

``semantic_hash`` is ``"sha256:" + hexdigest`` over JSON (``sort_keys=True``, separators
``(",", ":")``) of an object with four keys:

- ``verb``: the verb name;
- ``operands``: one nested list per operand. A name is ``[text, quoted]`` with ``text``
  exactly as written (no case folding: warehouse case rules differ). A table is
  ``[catalog, schema, table]`` with an empty slot as ``null``; a column is
  ``[table, name]``; an edge is ``[left_column, right_column]``;
- ``attrs``: ``[name, op, value_text]`` sorted by name, ``op`` canonical (``=`` -> ``==``,
  ``null`` when bare) and ``value_text`` from :func:`value_text`;
- ``filter``: :func:`factlang.semantics.filters.canonical` of the filter, or ``null``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal

from factlang.semantics.checked import CheckedAttr
from factlang.semantics.filters import FilterExpr, canonical
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef
from factlang.syntax.formatter import format_number

Operand = TableRef | ColumnRef | EdgeRef


def _ident(i: Ident | None) -> list[object] | None:
    return None if i is None else [i.text, i.quoted]


def _table(t: TableRef) -> list[object]:
    return [_ident(t.catalog), _ident(t.schema), _ident(t.table)]


def _column(c: ColumnRef) -> list[object]:
    return [_table(c.table), _ident(c.column)]


def operand_json(op: Operand) -> list[object]:
    if isinstance(op, TableRef):
        return _table(op)
    if isinstance(op, ColumnRef):
        return _column(op)
    return [_column(op.left), _column(op.right)]


def value_text(value: object) -> str:
    """Canonical text of a coerced attribute value.

    Numbers print without exponent or trailing zeros (``0.990`` and ``99%`` agree), strings
    are JSON-quoted (so ``'1'`` differs from ``1``), names are ``[text, quoted]``, temporals
    ISO-8601 (timestamps in UTC) and sequences ``[item,item]``.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, Decimal)):
        return format_number(Decimal(value))
    if isinstance(value, dt.datetime):
        aware = value.replace(tzinfo=dt.timezone.utc) if value.tzinfo is None else value
        return aware.astimezone(dt.timezone.utc).isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, Ident):
        return json.dumps([value.text, value.quoted], ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (tuple, list)):
        return "[" + ",".join(value_text(v) for v in value) + "]"
    if isinstance(value, float):
        return repr(value)
    return json.dumps(str(value), ensure_ascii=False)


def _op(op: str | None) -> str | None:
    return "==" if op == "=" else op


def semantic_hash(
    verb: str,
    operands: tuple[Operand, ...],
    attrs: Mapping[str, CheckedAttr],
    filter: FilterExpr | None,
) -> str:
    payload = {
        "verb": verb,
        "operands": [operand_json(o) for o in operands],
        "attrs": [
            [name, _op(attrs[name].op), value_text(attrs[name].value)] for name in sorted(attrs)
        ],
        "filter": canonical(filter) if filter is not None else None,
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


__all__ = ["operand_json", "semantic_hash", "value_text"]
