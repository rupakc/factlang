"""The standard verb pack (spec 6): ``standard_pack()`` returns a fresh pack each call."""

from __future__ import annotations

from importlib import import_module

from factlang.contract import Contract, ContractPack

PACK_NAME = "standard"
PACK_VERSION = "1.0"

# Verb modules, each exporting ``CONTRACT``. Later tasks append their modules here.
_MODULES: tuple[str, ...] = (
    "exists",
    "type",
    "rows",
    "nulls",
    "dead",
    "unique",
    "fanout",
)


def standard_pack() -> ContractPack:
    """A fresh ``("standard", "1.0")`` pack holding every verb in ``_MODULES``."""
    pack = ContractPack(PACK_NAME, PACK_VERSION)
    for module in _MODULES:
        contract = import_module(f"{__name__}.{module}").CONTRACT
        assert isinstance(contract, Contract)
        pack.register(contract)
    return pack


__all__ = ["PACK_NAME", "PACK_VERSION", "standard_pack"]
