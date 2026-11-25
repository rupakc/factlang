"""``needs_quoting`` / ``quote_name`` against the real parser (0.1.3 spec 3.3 P1, P2)."""

from __future__ import annotations

from hypothesis import example, given
from hypothesis import strategies as st

import factlang as fl
from factlang.semantics.filters import FIsNull
from factlang.semantics.refs import Ident
from factlang.syntax import FILTER_KEYWORDS, needs_quoting, parse, quote_name
from factlang.syntax.ast import Claim, Ref

_UNREPRESENTABLE = "\r\n\x00"

# identifier-shaped names in every case mix, the reserved words, and arbitrary text
_ascii_words = st.from_regex(r"[A-Za-z_$0-9][A-Za-z0-9_$]{0,6}", fullmatch=True)
_reserved = st.sampled_from(sorted({*FILTER_KEYWORDS, "WHERE", "USE"}))
_mixed = st.text(alphabet="aZ_$9 .-#'\"`\\é", min_size=0, max_size=6)
_any_text = st.text(max_size=8)
candidate_names = st.one_of(_ascii_words, _reserved, _reserved.map(str.lower), _mixed, _any_text)

# printable names (spec P2's property) and, beyond it, everything quote_name accepts
_printable = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=1, max_size=12
)
_representable = st.text(
    alphabet=st.characters(blacklist_characters=_UNREPRESENTABLE), min_size=1, max_size=12
)


def _bare_claim_part(name: str) -> bool:
    """Does ``ROWS <name> VALUE == 1`` parse to one operand whose one part is ``name`` bare?"""
    result = parse(f"ROWS {name} VALUE == 1")
    if result.diagnostics.has_errors or len(result.document.items) != 1:
        return False
    claim = result.document.items[0]
    if not isinstance(claim, Claim) or len(claim.operands) != 1:
        return False
    ref = claim.operands[0]
    return isinstance(ref, Ref) and [(p.text, p.quoted) for p in ref.parts] == [(name, False)]


def _bare_filter_column(name: str) -> bool:
    """Does ``<name> IS NULL`` parse (as a filter) to ``IS NULL`` on exactly ``name`` bare?"""
    flt, bag = fl.parse_filter(f"{name} IS NULL")
    return not bag.has_errors and flt == FIsNull(Ident(name, quoted=False))


def _ascii(name: str) -> bool:
    return all(ord(c) < 128 for c in name)


@given(candidate_names)
@example("deleted")
@example("Status")
@example("order")
@example("ID")
@example("NULL")
@example("order items")
@example("2fa")
def test_a_bare_name_needing_no_quotes_parses_to_exactly_itself(name: str) -> None:
    if not needs_quoting(name):
        assert _bare_claim_part(name), name
        assert _bare_filter_column(name), name


@given(candidate_names.filter(_ascii))
def test_for_ascii_names_needs_quoting_is_exact(name: str) -> None:
    # The rule is ASCII by design (a non-ASCII letter is quoted although the lexer would
    # accept it bare); over ASCII it says exactly when the bare form fails somewhere.
    bare_ok = _bare_claim_part(name) and _bare_filter_column(name)
    assert needs_quoting(name) is (not bare_ok), name


@given(_printable)
def test_quote_name_round_trips_as_a_filter_column(name: str) -> None:
    flt, bag = fl.parse_filter(f"{quote_name(name)} IS NULL")
    assert not bag.has_errors, bag
    assert isinstance(flt, FIsNull)
    assert flt.column == Ident(name, quoted=True)


@given(_representable)
@example("\ufeffbom")
@example("\t\x0b\x0c\u2028\x85")
def test_every_representable_name_round_trips_in_a_claim_and_a_filter(name: str) -> None:
    flt, bag = fl.parse_filter(f"{quote_name(name)} IS NULL")
    assert not bag.has_errors and flt == FIsNull(Ident(name, quoted=True))
    q = quote_name(name)
    result = parse(f"ROWS {q}.{q} VALUE == 1")
    assert not result.diagnostics.has_errors
    (claim,) = result.document.items
    assert isinstance(claim, Claim) and isinstance(claim.operands[0], Ref)
    assert [(p.text, p.quoted) for p in claim.operands[0].parts] == [(name, True)] * 2


@given(st.text(max_size=6), st.sampled_from(_UNREPRESENTABLE), st.text(max_size=6))
def test_quote_name_refuses_cr_lf_and_nul_anywhere(before: str, bad: str, after: str) -> None:
    try:
        quote_name(before + bad + after)
    except ValueError:
        return
    raise AssertionError("quote_name accepted an unrepresentable name")
