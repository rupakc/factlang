"""``TYPE t.c IS integer``: the column's normalised type family (spec 6.1)."""

from __future__ import annotations

from collections.abc import Mapping

from factlang.contract import Contract, Example
from factlang.judge import Compare
from factlang.semantics.catalog import Catalog
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import AttrSpec, Operand, VerbSpec

SPEC = VerbSpec(
    "TYPE",
    "The column's type family equals the stated family.",
    (Operand.column(),),
    attrs=(AttrSpec("IS", "TYPE_FAMILY", required=True, doc="expected type family"),),
)


def catalog_facts(claim: CheckedClaim, catalog: Catalog) -> dict[str, object]:
    """``None`` facts when the catalog cannot answer (judged ``NO_DATA``, never FAIL)."""
    columns = catalog.columns(claim.table)
    actual: str | None = None
    if columns is not None:
        want = claim.column.column
        if want.quoted:
            actual = columns.get(want.text)
        else:
            actual = next((t for n, t in columns.items() if n.lower() == want.text.lower()), None)
    family = None if actual is None else actual.lower()
    return {"actual_type": actual, "family": family}


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {"actual_type": payload.get("actual_type"), "family": payload.get("family")}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=Compare("family", "==", attr="IS"),
    examples=(
        Example("TYPE orders.id IS integer"),
        Example("TYPE orders.total IS decimal"),
        Example("TYPE events.created_at IS timestamp"),
        Example("TYPE orders.id", valid=False, expect="FL304"),
        Example("TYPE orders.id IS text", valid=False, expect="FL309"),
        Example("TYPE orders IS integer", valid=False, expect="FL302"),
        Example("TYPE orders.id IS == integer", valid=False, expect="FL306"),
    ),
    catalog_facts=catalog_facts,
    fact_names=("actual_type", "family"),
)
