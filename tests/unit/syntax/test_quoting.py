"""``factlang.syntax.needs_quoting`` / ``quote_name`` (0.1.3 spec 3.3 P1, P2): examples."""

from __future__ import annotations

import pytest

import factlang as fl
from factlang.semantics.filters import FIsNull
from factlang.semantics.refs import Ident
from factlang.syntax import FILTER_KEYWORDS, needs_quoting, parse, quote_name
from factlang.syntax.ast import Claim, Ref


@pytest.mark.parametrize(
    "name", ["deleted", "Status", "order", "_", "_1", "a$b", "x9", "camelCase", "where", "use"]
)
def test_bare_names(name: str) -> None:
    assert needs_quoting(name) is False


@pytest.mark.parametrize(
    "name",
    [
        "ID",
        "NULL",
        "order items",
        "2fa",
        "",
        "WHERE",
        "USE",
        "ORDERS",
        "A1",
        "A_B",
        "$a",
        "a-b",
        "a.b",
        "é",  # outside [A-Za-z_]: the spec rule is ASCII, quoting is always safe
        "a\n",
    ],
)
def test_names_that_need_quoting(name: str) -> None:
    assert needs_quoting(name) is True


def test_every_filter_keyword_needs_quoting() -> None:
    for kw in FILTER_KEYWORDS:
        assert needs_quoting(kw), kw
        assert not needs_quoting(kw.lower()), kw  # the keyword check is case-sensitive


@pytest.mark.parametrize(
    ("name", "quoted"),
    [
        ("order items", '"order items"'),
        ('say "hi"', '"say ""hi"""'),
        ('"', '""""'),
        ("NULL", '"NULL"'),
        ("deleted", '"deleted"'),
        ("tab\there", '"tab\there"'),
    ],
)
def test_quote_name(name: str, quoted: str) -> None:
    assert quote_name(name) == quoted


@pytest.mark.parametrize("name", ["", "a\rb", "a\nb", "\r\n", "nul\x00"])
def test_quote_name_refuses_unrepresentable_names(name: str) -> None:
    with pytest.raises(ValueError):
        quote_name(name)


def test_quoted_name_round_trips_through_the_parser() -> None:
    name = 'Order "Items" NULL'
    flt, bag = fl.parse_filter(f"{quote_name(name)} IS NULL")
    assert not bag.has_errors and flt == FIsNull(Ident(name, quoted=True))
    result = parse(f"ROWS {quote_name(name)} VALUE == 1")
    (claim,) = result.document.items
    assert isinstance(claim, Claim) and isinstance(claim.operands[0], Ref)
    (part,) = claim.operands[0].parts
    assert (part.text, part.quoted) == (name, True)


def test_helpers_are_public_syntax_api() -> None:
    from factlang import syntax

    assert {"needs_quoting", "quote_name"} <= set(syntax.__all__)
