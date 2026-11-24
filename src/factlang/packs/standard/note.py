"""``NOTE t.c IS unit BECAUSE '...'``: a recorded semantic fact, not measured (spec 6.5)."""

from __future__ import annotations

from collections.abc import Mapping

from factlang.contract import Contract, Example
from factlang.judge import Always
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import AttrSpec, Operand, VerbSpec

SPEC = VerbSpec(
    "NOTE",
    "A semantic fact about the column, recorded with its reason and never measured.",
    (Operand.column(),),
    attrs=(
        AttrSpec("IS", "SYMBOL", required=True, doc="the stated semantic (e.g. a unit)"),
        AttrSpec("BECAUSE", "STRING", required=True, doc="why this holds"),
    ),
)


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=Always(),
    examples=(
        Example("NOTE order_items.price IS eur BECAUSE 'prices are stored in euro'"),
        Example("NOTE orders.total IS 'net of tax' BECAUSE 'finance confirmed'"),
        Example("NOTE events.at IS utc BECAUSE 'the tracker writes UTC'"),
        Example("NOTE order_items.price IS eur", valid=False, expect="FL304"),
        Example("NOTE order_items.price BECAUSE 'x'", valid=False, expect="FL304"),
        Example("NOTE order_items IS eur BECAUSE 'x'", valid=False, expect="FL302"),
        Example("NOTE order_items.price IS eur BECAUSE 5", valid=False, expect="FL305"),
        Example(
            "NOTE order_items.price IS eur BECAUSE 'x' WHERE price > 0", valid=False, expect="FL308"
        ),
    ),
    unprobeable_reason="semantic fact; recorded, not measured",
    fact_names=(),
)
