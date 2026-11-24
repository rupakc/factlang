"""semantic_hash: canonical serialisation of a resolved claim (spec 4.6)."""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from factlang.diagnostics import DiagnosticBag
from factlang.semantics.analyzer import analyze
from factlang.semantics.checked import CheckedAttr
from factlang.semantics.filters import FBool, FCompare, Param, canonical
from factlang.semantics.identity import semantic_hash
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef
from factlang.semantics.signature import (
    AttrSpec,
    OperandKind,
    OperandSpec,
    VerbRegistry,
    VerbSpec,
)
from factlang.syntax import parse
from factlang.syntax.formatter import format_document

CMP = (">=", "==", "<=", "<", ">")
REG = VerbRegistry()
REG.register(
    VerbSpec(
        "JOIN",
        "key overlap",
        (OperandSpec("edge", OperandKind.EDGE),),
        (
            AttrSpec("MATCH", "RATIO", required=True, ops=CMP),
            AttrSpec("CARD", "CARDINALITY"),
            AttrSpec("WITH", "COLUMN_LIST"),
            AttrSpec("TO", "COLUMN_LIST"),
        ),
        filterable=True,
    )
)
REG.register(
    VerbSpec(
        "NULLS",
        "null rate",
        (OperandSpec("column", OperandKind.COLUMN),),
        (AttrSpec("RATE", "RATIO", required=True, ops=CMP),),
        filterable=True,
    )
)
REG.register(
    VerbSpec(
        "ROWS",
        "row count",
        (OperandSpec("table", OperandKind.TABLE),),
        (AttrSpec("VALUE", "INTEGER", required=True, ops=(*CMP, "!=")),),
        filterable=True,
    )
)


def h(src: str) -> str:
    bag = DiagnosticBag()
    claims = analyze(parse(src).document, REG, bag=bag)
    assert not bag.has_errors, bag.render(src)
    assert len(claims) == 1
    return claims[0].semantic_hash


def test_exact_serialisation() -> None:
    col = ColumnRef(TableRef(None, Ident("S", True), Ident("orders")), Ident("Id"))
    attrs = {
        "RATE": CheckedAttr("=", Decimal("0.050"), "RATIO"),
        "CARD": CheckedAttr(None, "N:1", "CARDINALITY"),
    }
    flt = FBool(Ident("deleted"))
    expected = json.dumps(
        {
            "verb": "NULLS",
            "operands": [[[None, ["S", True], ["orders", False]], ["Id", False]]],
            "attrs": [["CARD", None, '"N:1"'], ["RATE", "==", "0.05"]],
            "filter": canonical(flt),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    want = "sha256:" + hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert semantic_hash("NULLS", (col,), attrs, flt) == want


def test_edge_and_no_filter_serialise_as_nested_lists_and_null() -> None:
    lhs = ColumnRef(TableRef(None, None, Ident("o")), Ident("a"))
    rhs = ColumnRef(TableRef(None, None, Ident("c")), Ident("b"))
    expected = json.dumps(
        {
            "verb": "JOIN",
            "operands": [
                [
                    [[None, None, ["o", False]], ["a", False]],
                    [[None, None, ["c", False]], ["b", False]],
                ]
            ],
            "attrs": [],
            "filter": None,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    assert semantic_hash("JOIN", (EdgeRef(lhs, rhs),), {}, None) == (
        "sha256:" + hashlib.sha256(expected.encode()).hexdigest()
    )


def test_hash_ignores_comment_whitespace_attr_order_numeric_spelling() -> None:
    base = h("JOIN orders.a -> customers.b MATCH >=0.99 CARD N:1")
    assert base == h("JOIN   orders.a ->customers.b   CARD N:1 MATCH >= 99%   # why")
    assert base == h("JOIN orders.a -> customers.b \\\n  MATCH >=0.990 CARD N:1")
    assert h("ROWS orders VALUE =1_717") == h("ROWS orders VALUE == 1717")
    assert h("ROWS orders VALUE ==1 WHERE x = 1.0") == h("ROWS orders VALUE ==1 WHERE x == 1")


def test_hash_ignores_filter_order_and_param_names() -> None:
    assert h("ROWS orders VALUE >=1 WHERE a = 1 AND b = 'x'") == h(
        "ROWS orders VALUE >=1 WHERE b = 'x' AND a = 1"
    )
    assert h("ROWS orders VALUE >=1 WHERE a IN [2, 1]") == h(
        "ROWS orders VALUE >=1 WHERE a IN [1, 2]"
    )


def test_hash_changes_on_value_op_operand_filter() -> None:
    base = h("NULLS orders.status RATE <=5%")
    assert base != h("NULLS orders.status RATE <=6%")  # value
    assert base != h("NULLS orders.status RATE <5%")  # op
    assert base != h("NULLS orders.state RATE <=5%")  # operand
    assert base != h("NULLS customers.status RATE <=5%")  # operand table
    assert base != h('NULLS orders."status" RATE <=5%')  # quoted differs from bare
    assert base != h("NULLS orders.Status RATE <=5%")  # no case folding of operands
    assert base != h("NULLS orders.status RATE <=5% WHERE x = 1")  # filter
    assert h("NULLS orders.status RATE <=5% WHERE x = 1") != h(
        "NULLS orders.status RATE <=5% WHERE x = 2"
    )
    assert h("NULLS orders.status RATE <=5% WHERE x = 1") != h(
        "NULLS orders.status RATE <=5% WHERE x = '1'"
    )
    assert h("ROWS orders VALUE >=1") != h("ROWS orders VALUE ==1")


def test_hash_same_under_equivalent_use() -> None:
    assert h("USE s\nNULLS t.c RATE <=1%") == h("NULLS s.t.c RATE <=1%")
    assert h("USE c.s\nNULLS t.c RATE <=1%") == h("NULLS c.s.t.c RATE <=1%")
    assert h("USE c.s\nNULLS x.t.c RATE <=1%") == h("NULLS c.x.t.c RATE <=1%")
    assert h("USE c.s\nJOIN a.x -> b.y MATCH >=1%") == h("JOIN c.s.a.x -> c.s.b.y MATCH >=1%")
    assert h("USE s\nNULLS t.c RATE <=1%") != h("NULLS t.c RATE <=1%")  # negative control


def test_bare_bool_equals_explicit_true_end_to_end() -> None:
    assert h("ROWS orders VALUE >=1 WHERE deleted") == h(
        "ROWS orders VALUE >=1 WHERE deleted = TRUE"
    )
    t = Param("p1", True, "boolean")
    assert semantic_hash("ROWS", (), {}, FBool(Ident("d"))) == semantic_hash(
        "ROWS", (), {}, FCompare(Ident("d"), "=", t)
    )


def test_format_roundtrip_keeps_hash() -> None:
    src = "USE shop\nJOIN o.a->c.b   WITH [x] TO [y] MATCH >= 0.5 WHERE NOT deleted OR k IN [1,2]"
    bag = DiagnosticBag()
    a = analyze(parse(src).document, REG, bag=bag)
    b = analyze(parse(format_document(parse(src).document)).document, REG, bag=bag)
    assert not bag.has_errors
    assert [c.semantic_hash for c in a] == [c.semantic_hash for c in b]
    # the canonical text of a checked claim hashes the same on its own (USE resolved)
    c = analyze(parse(a[0].canonical_text).document, REG, bag=bag)
    assert c[0].semantic_hash == a[0].semantic_hash
