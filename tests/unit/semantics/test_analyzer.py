"""Analyzer: AST + registry + catalog -> CheckedClaims and FL3xx diagnostics (spec 4, 6, 12)."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from factlang.diagnostics import DiagnosticBag, Severity
from factlang.semantics.analyzer import analyze
from factlang.semantics.catalog import Catalog, NullCatalog, StaticCatalog
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.filters import (
    FAnd,
    FBetween,
    FBool,
    FCompare,
    FIn,
    FIsNull,
    FNot,
    FOr,
    Param,
    canonical,
)
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef
from factlang.semantics.signature import (
    AttrSpec,
    OperandKind,
    OperandSpec,
    VerbRegistry,
    VerbSpec,
)
from factlang.syntax import parse

CMP = (">=", "==", "<=", "<", ">")
ALL = ("==", "!=", ">=", "<=", "<", ">")


@dataclass(frozen=True)
class XVerbSpec(VerbSpec):
    """Stand-in for Task 12's ``VerbSpec.exclusive``."""

    exclusive: tuple[tuple[str, str], ...] = ()


def registry() -> VerbRegistry:
    reg = VerbRegistry()
    reg.register(
        VerbSpec(
            "JOIN",
            "key overlap",
            (OperandSpec("edge", OperandKind.EDGE),),
            (
                AttrSpec("MATCH", "RATIO", required=True, ops=CMP),
                AttrSpec("CARD", "CARDINALITY"),
                AttrSpec("CAST", "TYPE_FAMILY"),
                AttrSpec("COVERAGE", "RATIO", ops=CMP),
                AttrSpec("WITH", "COLUMN_LIST"),
                AttrSpec("TO", "COLUMN_LIST"),
            ),
            filterable=True,
        )
    )
    reg.register(
        VerbSpec(
            "NULLS",
            "null rate",
            (OperandSpec("column", OperandKind.COLUMN),),
            (AttrSpec("RATE", "RATIO", required=True, ops=CMP),),
            filterable=True,
        )
    )
    reg.register(
        VerbSpec(
            "VALUES",
            "membership",
            (OperandSpec("column", OperandKind.COLUMN),),
            (
                AttrSpec("IN", "VALUE_LIST", required=True),
                AttrSpec("SPLIT", "SEPARATOR"),
                AttrSpec("ALLOW", "RATIO", ops=("<=",)),
                AttrSpec("REASON", "STRING"),
            ),
            filterable=True,
            requires=(("ALLOW", "REASON"),),
        )
    )
    reg.register(
        VerbSpec(
            "ROWS",
            "row count",
            (OperandSpec("table", OperandKind.TABLE),),
            (AttrSpec("VALUE", "INTEGER", required=True, ops=ALL),),
            filterable=True,
        )
    )
    reg.register(
        VerbSpec(
            "UNIQUE",
            "uniqueness",
            (OperandSpec("column", OperandKind.COLUMN),),
            (AttrSpec("WITH", "COLUMN_LIST"),),
            filterable=True,
        )
    )
    reg.register(
        VerbSpec(
            "RANGE",
            "bounds",
            (OperandSpec("column", OperandKind.COLUMN),),
            (AttrSpec("BETWEEN", "RANGE_PAIR", required=True),),
        )
    )
    reg.register(
        XVerbSpec(
            "SUM",
            "sum",
            (OperandSpec("column", OperandKind.COLUMN),),
            (
                AttrSpec("VALUE", "NUMBER", required=True, ops=ALL),
                AttrSpec("TOLERANCE", "RATIO"),
                AttrSpec("ABS_TOLERANCE", "NUMBER"),
            ),
            filterable=True,
            exclusive=(("TOLERANCE", "ABS_TOLERANCE"),),
        )
    )
    return reg


REG = registry()

SHOP = StaticCatalog(
    {
        "shop.public.orders": {
            "id": "INTEGER",
            "customer_id": "BIGINT",
            "shop_id": "INTEGER",
            "status": "VARCHAR",
            "total": "DECIMAL(12,2)",
            "deleted": "BOOLEAN",
            "created_at": "TIMESTAMP",
            "code": "VARCHAR",
        },
        "shop.public.customers": {
            "id": "BIGINT",
            "shop_id": "INTEGER",
            "code": "INTEGER",
            "email": "VARCHAR",
        },
    }
)


def run(src: str, catalog: Catalog | None = None) -> tuple[list[CheckedClaim], DiagnosticBag]:
    res = parse(src)
    assert not res.diagnostics.has_errors, res.diagnostics.render(src)
    bag = DiagnosticBag()
    return analyze(res.document, REG, catalog=catalog, bag=bag), bag


def one(src: str, catalog: Catalog | None = None) -> CheckedClaim:
    claims, bag = run(src, catalog)
    assert not bag.has_errors, bag.render(src)
    assert len(claims) == 1
    return claims[0]


def codes(src: str, catalog: Catalog | None = None) -> list[str]:
    return run(src, catalog)[1].codes()


def h(src: str, catalog: Catalog | None = None) -> str:
    return one(src, catalog).semantic_hash


def T(*parts: str) -> TableRef:
    padded: list[Ident | None] = [None] * (3 - len(parts)) + [Ident(p) for p in parts]
    return TableRef(padded[0], padded[1], padded[2])  # type: ignore[arg-type]


# -- clean claims ---------------------------------------------------------------------


def test_valid_claims_resolve() -> None:
    c = one("NULLS orders.status RATE <=5%")
    assert c.verb == "NULLS"
    assert c.operands == (ColumnRef(T("orders"), Ident("status")),)
    assert c.attrs["RATE"].op == "<="
    assert c.attrs["RATE"].value == Decimal("0.05")
    assert c.attrs["RATE"].type == "RATIO"
    assert c.semantic_hash.startswith("sha256:") and len(c.semantic_hash) == 71
    assert c.source_text == "NULLS orders.status RATE <=5%"


def test_attr_equals_is_canonical_double_equals() -> None:
    assert one("ROWS orders VALUE =3").attrs["VALUE"].op == "=="


def test_comment_and_canonical_text() -> None:
    c = one("USE shop\nNULLS orders.status RATE <= 5%  # note")
    assert c.comment == "note"
    assert c.canonical_text == "NULLS shop.orders.status RATE <= 5%"


def test_column_list_values_are_idents() -> None:
    c = one('UNIQUE orders.id WITH [shop_id, "Region"]')
    assert c.attrs["WITH"].value == (Ident("shop_id"), Ident("Region", True))


def test_quoted_operand_keeps_quoted_flag() -> None:
    c = one('NULLS "ORDERS".id RATE <=1%')
    assert c.column.table.table == Ident("ORDERS", True)


# -- USE ------------------------------------------------------------------------------


def test_use_fills_missing_leading_slots_right_aligned() -> None:
    claims, bag = run(
        "USE shop.public\nROWS orders VALUE >=1\nROWS x.orders VALUE >=1\n"
        "ROWS a.b.orders VALUE >=1\nUSE other\nROWS orders VALUE >=1"
    )
    assert not bag.has_errors
    assert [c.table for c in claims] == [
        T("shop", "public", "orders"),
        T("shop", "x", "orders"),
        T("a", "b", "orders"),
        T("other", "orders"),
    ]


def test_use_applies_to_both_edge_sides() -> None:
    c = one("USE shop.public\nJOIN orders.customer_id -> customers.id MATCH >=99%")
    assert c.edge == EdgeRef(
        ColumnRef(T("shop", "public", "orders"), Ident("customer_id")),
        ColumnRef(T("shop", "public", "customers"), Ident("id")),
    )


def test_use_with_too_many_parts_is_fl302() -> None:
    assert codes("USE a.b.c\nROWS orders VALUE >=1") == ["FL302"]


# -- FL300 - FL309 --------------------------------------------------------------------


def test_fl300_unknown_verb_with_suggestion() -> None:
    claims, bag = run("NULSS orders.id RATE <=1%")
    assert claims == []
    (d,) = list(bag)
    assert d.code == "FL300" and d.suggestion == "NULLS"


def test_fl301_wrong_operand_count() -> None:
    assert codes("NULLS orders.id orders.status RATE <=1%") == ["FL301"]
    assert codes("NULLS RATE <=1%") == ["FL301"]


def test_fl302_wrong_operand_kind_or_depth() -> None:
    assert codes("NULLS orders RATE <=1%") == ["FL302"]  # column needs >=2 parts
    assert codes("NULLS a.b.c.d.e RATE <=1%") == ["FL302"]  # 5+ parts
    assert codes("ROWS a.b.c.d VALUE >=1") == ["FL302"]  # table <=3 parts
    assert codes("ROWS a -> b VALUE >=1") == ["FL302"]  # edge where table expected
    assert codes("JOIN orders.id MATCH >=1%") == ["FL302"]  # ref where edge expected
    assert codes("JOIN orders -> customers.id MATCH >=1%") == ["FL302"]
    assert codes("ROWS 'x' VALUE >=1") == ["FL302"]  # literal operand
    assert codes("NULLS a.b.c.d RATE <=1%") == []  # 4 parts is a column ref


def test_fl303_unknown_attribute_with_suggestion() -> None:
    claims, bag = run("NULLS orders.id RAET <=1%")
    assert claims == []
    d = next(iter(bag))
    assert d.code == "FL303" and d.suggestion == "RATE"


def test_fl304_missing_required() -> None:
    assert codes("NULLS orders.id") == ["FL304"]


def test_fl304_requires_pair() -> None:
    assert codes("VALUES orders.status IN ['a'] ALLOW <=1%") == ["FL304"]
    assert codes("VALUES orders.status IN ['a'] ALLOW <=1% REASON 'x'") == []


def test_fl304_join_with_without_to_names_the_missing_one() -> None:
    claims, bag = run("JOIN orders.id -> customers.id WITH [shop_id] MATCH >=99%")
    assert bag.codes() == ["FL304"] and claims == []
    assert "TO" in next(iter(bag)).message
    _, bag = run("JOIN orders.id -> customers.id TO [shop_id] MATCH >=99%")
    assert bag.codes() == ["FL304"]
    assert "WITH" in next(iter(bag)).message


def test_fl309_join_with_to_unequal_length() -> None:
    assert codes("JOIN orders.id -> customers.id WITH [a, b] TO [c] MATCH >=99%") == ["FL309"]
    assert codes("JOIN orders.id -> customers.id WITH [a, b] TO [c, d] MATCH >=99%") == []


def test_fl305_wrong_value_type() -> None:
    assert codes("NULLS orders.id RATE <='x'") == ["FL305"]
    assert codes("RANGE orders.total BETWEEN 3") == ["FL305"]


def test_fl306_operator_rules() -> None:
    assert codes("NULLS orders.id RATE 5%") == ["FL306"]  # operator required
    assert codes("NULLS orders.id RATE !=5%") == ["FL306"]  # operator not allowed
    assert codes("JOIN a.x -> b.y MATCH >=1% CARD ==N:1") == ["FL306"]  # operator forbidden
    assert codes("VALUES orders.status IN ['a'] ALLOW =1% REASON 'x'") == ["FL306"]


def test_fl307_column_list_repeats_key_column() -> None:
    assert codes("UNIQUE orders.id WITH [shop_id, id]") == ["FL307"]
    assert codes("UNIQUE orders.id WITH [shop_id, shop_id]") == ["FL307"]
    assert codes('UNIQUE orders.id WITH [shop_id, "ID"]') == ["FL307"]  # case-insensitive
    assert codes('UNIQUE orders.id WITH [shop_id, "Shop_Id"]') == ["FL307"]
    assert codes("JOIN o.a -> c.b WITH [a] TO [x] MATCH >=1%") == ["FL307"]
    assert codes("JOIN o.a -> c.b WITH [x] TO [b] MATCH >=1%") == ["FL307"]
    assert codes("JOIN o.a -> c.b WITH [b] TO [a] MATCH >=1%") == []


def test_fl308_filter_not_allowed() -> None:
    assert codes("RANGE orders.total BETWEEN [0, 1] WHERE x = 1") == ["FL308"]


def test_fl309_invalid_values() -> None:
    assert codes("NULLS orders.id RATE <=150%") == ["FL309"]
    assert codes("RANGE orders.total BETWEEN [5, 1]") == ["FL309"]
    assert codes("RANGE orders.created_at BETWEEN [DATE '2025-02-30', DATE '2025-03-01']") == [
        "FL309"
    ]
    assert codes("ROWS orders VALUE >=1 WHERE d = DATE '2025-13-01'") == ["FL309"]
    assert codes("ROWS orders VALUE >=1 WHERE t < TIMESTAMP 'noon'") == ["FL309"]
    assert codes("ROWS orders VALUE >=1.5") == ["FL309"]


def test_fl309_exclusive_pair() -> None:
    assert codes("SUM orders.total VALUE ==1 TOLERANCE 1% ABS_TOLERANCE 0.5") == ["FL309"]
    assert codes("SUM orders.total VALUE ==1 TOLERANCE 1%") == []


def test_errors_on_one_claim_do_not_drop_others() -> None:
    claims, bag = run("NULLS orders.id\nROWS orders VALUE >=1\nFOO x")
    assert bag.codes() == ["FL304", "FL300"]
    assert [c.verb for c in claims] == ["ROWS"]


def test_diagnostic_spans_point_at_the_culprit() -> None:
    _, bag = run("ROWS orders VALUE >=1\nNULLS orders.id RAET <=1%")
    d = next(iter(bag))
    assert d.span is not None and (d.span.line, d.span.col) == (2, 17)


# -- filters --------------------------------------------------------------------------


def test_filter_params_named_in_source_order_and_typed() -> None:
    c = one(
        "ROWS orders VALUE >=1 WHERE a = 1 AND (b IN ['x', 'y'] OR c BETWEEN 1.5 AND 2)"
        " AND NOT d = TRUE AND e >= DATE '2025-01-31' AND f < TIMESTAMP '2025-01-31T12:00:00+02:00'"
        " AND g IS NOT NULL AND deleted"
    )
    f = c.filter
    assert isinstance(f, FAnd)
    a, orr, notd, e, ff, g, b = f.items
    assert a == FCompare(Ident("a"), "=", Param("p1", 1, "integer"))
    assert isinstance(orr, FOr)
    assert orr.items[0] == FIn(
        Ident("b"), (Param("p2", "x", "string"), Param("p3", "y", "string")), False
    )
    assert orr.items[1] == FBetween(
        Ident("c"), Param("p4", Decimal("1.5"), "decimal"), Param("p5", 2, "integer")
    )
    assert notd == FNot(FCompare(Ident("d"), "=", Param("p6", True, "boolean")))
    assert e == FCompare(Ident("e"), ">=", Param("p7", dt.date(2025, 1, 31), "date"))
    assert ff == FCompare(
        Ident("f"),
        "<",
        Param("p8", dt.datetime(2025, 1, 31, 10, tzinfo=dt.timezone.utc), "timestamp"),
    )
    assert g == FIsNull(Ident("g"), True)
    assert b == FBool(Ident("deleted"))


def test_bare_boolean_hash_equals_explicit_true() -> None:
    assert h("ROWS orders VALUE >=1 WHERE deleted") == h(
        "ROWS orders VALUE >=1 WHERE deleted = TRUE"
    )
    assert h("ROWS orders VALUE >=1 WHERE NOT deleted") == h(
        "ROWS orders VALUE >=1 WHERE NOT deleted = TRUE"
    )
    # negative control
    assert h("ROWS orders VALUE >=1 WHERE deleted") != h(
        "ROWS orders VALUE >=1 WHERE deleted = FALSE"
    )


def test_fl305_bare_boolean_on_non_boolean_column_when_catalog_knows() -> None:
    src = "USE shop.public\nROWS orders VALUE >=1 WHERE status"
    assert codes(src, SHOP) == ["FL305"]
    assert codes("USE shop.public\nROWS orders VALUE >=1 WHERE deleted", SHOP) == []
    # unknown catalog: accepted, only FL313 info
    _, bag = run("ROWS orders VALUE >=1 WHERE status", NullCatalog())
    assert bag.codes() == ["FL313"] and not bag.has_errors


# -- catalog --------------------------------------------------------------------------


def test_fl311_definite_absence() -> None:
    assert codes("USE shop.public\nROWS nope VALUE >=1", SHOP) == ["FL311"]
    assert codes("USE shop.public\nNULLS orders.nope RATE <=1%", SHOP) == ["FL311"]
    assert codes("USE shop.public\nROWS orders VALUE >=1 WHERE nope = 1", SHOP) == ["FL311"]
    assert codes("USE shop.public\nUNIQUE orders.id WITH [nope]", SHOP) == ["FL311"]
    assert codes(
        "USE shop.public\nJOIN orders.id -> customers.id WITH [shop_id] TO [nope] MATCH >=1%",
        SHOP,
    ) == ["FL311"]
    # the analyser knows no dialect: any case variant counts, quoted or not
    assert codes("USE shop.public\nNULLS orders.ID RATE <=1%", SHOP) == []
    assert codes('USE shop.public\nNULLS orders."ID" RATE <=1%', SHOP) == []
    assert codes('USE shop.public\nNULLS orders."IDX" RATE <=1%', SHOP) == ["FL311"]


def test_fl305_bare_boolean_only_on_types_that_cannot_be_boolean() -> None:
    cat = StaticCatalog(
        {
            "t": {
                "tiny": "tinyint(1)",
                "u8": "UInt8",
                "n1": "NUMBER(1,0)",
                "odd": "geography",
                "f": "double",
                "d": "date",
                "s": "Nullable(String)",
            }
        }
    )
    for col in ("tiny", "u8", "n1", "odd"):
        assert codes(f"ROWS t VALUE >=1 WHERE {col}", cat) == [], col
    for col in ("f", "d", "s"):
        assert codes(f"ROWS t VALUE >=1 WHERE {col}", cat) == ["FL305"], col


def test_fl312_only_when_both_families_known() -> None:
    cat = StaticCatalog({"a": {"x": "geography", "i": "int"}, "b": {"y": "varchar"}})
    assert codes("JOIN a.x -> b.y MATCH >=1%", cat) == []
    assert codes("JOIN a.i -> b.y MATCH >=1%", cat) == ["FL312"]


def test_misbehaving_catalog_answers_count_as_unknown() -> None:
    class Weird:
        def __init__(self, exists: object, cols: object) -> None:
            self.e, self.c = exists, cols

        def table_exists(self, ref: TableRef) -> bool | None:
            return self.e  # type: ignore[return-value]

        def columns(self, ref: TableRef) -> dict[str, str] | None:
            return self.c  # type: ignore[return-value]

    for exists, cols in [
        ("yes", ["id"]),
        (1, {1: "int"}),
        (True, {"id": 5}),
        (True, "id"),
        (None, {"id": None}),
    ]:
        assert codes("NULLS t.id RATE <=1% WHERE deleted", Weird(exists, cols)) == ["FL313"]


def test_unhashable_attribute_value_is_fl305() -> None:
    from factlang.semantics.types import ValueType, register_type
    from factlang.syntax.ast import LiteralKind

    register_type(
        ValueType("WEIRD_SET", frozenset({LiteralKind.STRING}), lambda lit: {lit.value}),
        replace=True,
    )
    reg = VerbRegistry()
    reg.register(
        VerbSpec(
            "ODD",
            "odd",
            (OperandSpec("table", OperandKind.TABLE),),
            (AttrSpec("V", "WEIRD_SET"),),
        )
    )
    bag = DiagnosticBag()
    claims = analyze(parse("ODD t V 'x'").document, reg, bag=bag)
    assert claims == [] and bag.codes() == ["FL305"]


def test_non_finite_attribute_value_is_fl305() -> None:
    from factlang.semantics.types import ValueType, register_type
    from factlang.syntax.ast import LiteralKind

    for name, bad in (("NAN_T", Decimal("NaN")), ("INF_T", Decimal("-Infinity"))):
        register_type(
            ValueType(name, frozenset({LiteralKind.STRING}), lambda lit, b=bad: b),
            replace=True,
        )
        reg = VerbRegistry()
        reg.register(
            VerbSpec(
                "ODD", "odd", (OperandSpec("table", OperandKind.TABLE),), (AttrSpec("V", name),)
            )
        )
        bag = DiagnosticBag()
        claims = analyze(parse("ODD t V 'x'").document, reg, bag=bag)
        assert claims == [] and bag.codes() == ["FL305"], name


def test_fl313_once_per_claim_when_catalog_cannot_answer() -> None:
    _, bag = run(
        "JOIN orders.a -> customers.b WITH [x] TO [y] MATCH >=1% WHERE z = 1", NullCatalog()
    )
    assert bag.codes() == ["FL313"]
    assert next(iter(bag)).severity is Severity.INFO
    _, bag = run("ROWS a VALUE >=1\nROWS b VALUE >=1", NullCatalog())
    assert bag.codes() == ["FL313", "FL313"]


def test_no_catalog_means_no_name_checks() -> None:
    assert codes("ROWS orders VALUE >=1") == []


def test_fl312_cross_type_join_without_cast() -> None:
    src = "USE shop.public\nJOIN orders.code -> customers.code MATCH >=1%"
    claims, bag = run(src, SHOP)
    assert bag.codes() == ["FL312"] and len(claims) == 1
    assert next(iter(bag)).severity is Severity.WARNING
    assert codes(src + " CAST string", SHOP) == []
    # same family (INTEGER vs BIGINT) is fine
    assert codes("USE shop.public\nJOIN orders.customer_id -> customers.id MATCH >=1%", SHOP) == []


def test_catalog_that_raises_counts_as_unknown() -> None:
    class Boom:
        def table_exists(self, ref: TableRef) -> bool | None:
            raise RuntimeError("down")

        def columns(self, ref: TableRef) -> dict[str, str] | None:
            raise RuntimeError("down")

    assert codes("ROWS orders VALUE >=1", Boom()) == ["FL313"]


# -- never raises ---------------------------------------------------------------------

_verbs = st.sampled_from(["JOIN", "NULLS", "VALUES", "ROWS", "UNIQUE", "RANGE", "SUM", "FOO"])
_operands = st.sampled_from(
    ["orders", "orders.id", "o.x", "a.b.c.d.e", "o.id -> customers.id", "o -> c.y", "'s'",
     '"Q".id', "shop.public.orders.code", "orders.id orders.code", ""]
)  # fmt: skip
_attrs = st.sampled_from(
    ["RATE <=5%", "RATE 5%", "RATE <=150%", "MATCH >=99%", "MATCH 'x'", "WITH [a, b]",
     "WITH [id]", "TO [a]", "TO [x, x]", "IN ['x', 1]", "IN [1.5e2000]", "ALLOW <=1%",
     "REASON 'r'", "SPLIT ':'", "SPLIT 'abcd'", "CARD N:1", "CARD ==N:1", "CAST string",
     "CAST blob", "VALUE ==1", "VALUE >=1.5", "VALUE 3d", "BETWEEN [1, 0]",
     "BETWEEN [DATE '2025-01-01', 2]", "BETWEEN [DATE '2025-02-30', DATE '2025-03-01']",
     "TOLERANCE 1%", "ABS_TOLERANCE 0.5", "RAET <=1%", "COVERAGE >=50%"]
)  # fmt: skip
_filters = st.sampled_from(
    ["", "WHERE deleted", "WHERE NOT status", "WHERE x = 1", "WHERE id IN [1, 'a']",
     "WHERE d = DATE '2025-13-01'", "WHERE t < TIMESTAMP '2025-01-01T00:00:00Z'",
     "WHERE a BETWEEN 1 AND 2 OR (b IS NOT NULL AND NOT c)", "WHERE n = 1e5000",
     "WHERE nope = 1"]
)  # fmt: skip
_claims = st.builds(
    lambda v, o, a, f: " ".join(x for x in (v, o, *a, f) if x),
    _verbs,
    _operands,
    st.lists(_attrs, max_size=4, unique=True),
    _filters,
)
_lines = st.one_of(
    _claims,
    st.sampled_from(["USE shop.public", "USE x", "USE a.b.c"]),
    st.text(max_size=40),
)


class _Flaky:
    def table_exists(self, ref: TableRef) -> bool | None:
        return {"orders": True, "o": False}.get(ref.table.text)

    def columns(self, ref: TableRef) -> dict[str, str] | None:
        return {"id": "int", "x": "varchar", "deleted": "boolean"}


@settings(max_examples=400, deadline=None)
@given(st.lists(_lines, min_size=1, max_size=5), st.sampled_from([0, 1, 2, 3]))
def test_never_raises(lines: list[str], which: int) -> None:
    src = "\n".join(lines)
    doc = parse(src).document
    catalog: Catalog | None = [None, NullCatalog(), SHOP, _Flaky()][which]
    bag = DiagnosticBag()
    claims = analyze(doc, REG, catalog=catalog, bag=bag)
    assert all(c.semantic_hash.startswith("sha256:") for c in claims)
    assert all(d.code.startswith("FL3") for d in bag)


def test_canonical_of_bool_is_explicit_true() -> None:
    # guard the dependency the hash test relies on
    t = Param("p1", True, "boolean")
    assert canonical(FBool(Ident("d"))) == canonical(FCompare(Ident("d"), "=", t))
