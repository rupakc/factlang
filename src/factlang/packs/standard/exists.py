"""``EXISTS t`` / ``EXISTS t COLUMN c``: the table (or its column) exists (spec 6.1)."""

from __future__ import annotations

from collections.abc import Mapping

from factlang.contract import Contract, Example
from factlang.judge import Compare
from factlang.semantics.catalog import Catalog
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import AttrSpec, Operand, VerbSpec

SPEC = VerbSpec(
    "EXISTS",
    "The table, or the named column of it, exists in the catalog.",
    (Operand.table(),),
    attrs=(AttrSpec("COLUMN", "SYMBOL", doc="column that must exist in the table"),),
    existence_subject=True,
)


def catalog_facts(claim: CheckedClaim, catalog: Catalog) -> dict[str, object]:
    """``exists`` is ``None`` when the catalog cannot answer (judged ``NO_DATA``)."""
    table = catalog.table_exists(claim.table)
    if table is not True or not claim.has("COLUMN"):
        return {"exists": table}
    columns = catalog.columns(claim.table)
    if columns is None:
        return {"exists": None}
    want = claim.value("COLUMN")
    assert isinstance(want, str)
    # SYMBOL keeps no quoting: an exact name matches, otherwise case-insensitively
    return {"exists": want in columns or any(n.lower() == want.lower() for n in columns)}


def derive(payload: Mapping[str, object], claim: CheckedClaim) -> dict[str, object]:
    return {"exists": payload.get("exists")}


CONTRACT = Contract(
    spec=SPEC,
    derive=derive,
    decide=Compare("exists", "==", value=True),
    examples=(
        Example("EXISTS orders"),
        Example("EXISTS shop.orders"),
        Example("EXISTS orders COLUMN amount"),
        Example("EXISTS", valid=False, expect="FL301"),
        Example("EXISTS orders WHERE status = 'paid'", valid=False, expect="FL308"),
        Example("EXISTS orders COLUMN 5", valid=False, expect="FL305"),
        Example("EXISTS orders COLUMN >= amount", valid=False, expect="FL306"),
        Example("EXISTS orders.id.x.y", valid=False, expect="FL302"),
    ),
    catalog_facts=catalog_facts,
    fact_names=("exists",),
)
