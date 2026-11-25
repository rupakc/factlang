"""Contracts, contract packs and the extension API (spec 1.1 item 3, 10, 12 FL6xx; layer 10).

A ``Contract`` is one claim kind: its syntax (``VerbSpec``), how it is measured (exactly
one of ``plan``, ``catalog_facts`` or ``unprobeable_reason``), how facts are derived from
the measurement (``derive``), how they are judged (``decide``) and executable examples.
Contracts live in a named, versioned ``ContractPack``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from importlib.metadata import entry_points as _entry_points
from typing import Literal

from factlang.diagnostics import DiagnosticBag, Severity
from factlang.ir import CORE_PRIMITIVES, PRIMITIVE_LOWERINGS, ProbePlan, is_sampled
from factlang.judge import Derive, Predicate
from factlang.semantics.analyzer import analyze
from factlang.semantics.catalog import Catalog, NullCatalog
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.signature import AttrSpec, OperandSpec, VerbRegistry, VerbSpec
from factlang.syntax.parser import parse

Plan = Callable[[CheckedClaim], ProbePlan]
CatalogFacts = Callable[[CheckedClaim, Catalog], Mapping[str, object] | None]
OnConflict = Literal["error", "keep", "replace"]

PACK_ENTRY_POINT_GROUP = "factlang.packs"
MIN_VALID_EXAMPLES = 3
MIN_INVALID_EXAMPLES = 3
_CODE = re.compile(r"FL\d{3}")
_LEADING_WORD = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)")


def _rename_verb(source: str, old: str, new: str) -> str:
    """``source`` with its leading verb ``old`` spelled ``new`` (unchanged otherwise)."""
    m = _LEADING_WORD.match(source)
    if m is None or m.group(1) != old:
        return source
    return source[: m.start(1)] + new + source[m.end(1) :]


@dataclass(frozen=True)
class Example:
    """An executable example. An invalid one names the diagnostic code it must raise;
    a valid one may name a (non-error) diagnostic it must emit."""

    source: str
    valid: bool = True
    expect: str | None = None

    def __post_init__(self) -> None:
        if not self.valid and self.expect is None:
            raise ValueError(f"invalid example {self.source!r} must name its expect= code")
        if self.expect is not None and not _CODE.fullmatch(self.expect):
            raise ValueError(f"expect must be a diagnostic code FLnnn, got {self.expect!r}")


@dataclass(frozen=True)
class Contract:
    """One claim kind. Exactly one of ``plan``/``catalog_facts``/``unprobeable_reason``.

    ``fact_names`` declares every fact ``derive`` can produce (checked by FL600).
    """

    spec: VerbSpec
    derive: Derive
    decide: Predicate
    examples: tuple[Example, ...]
    plan: Plan | None = None
    catalog_facts: CatalogFacts | None = None
    unprobeable_reason: str | None = None
    fact_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.spec, VerbSpec):
            raise TypeError(f"Contract.spec must be a VerbSpec, got {self.spec!r}")
        if not callable(self.derive):
            raise TypeError(f"{self.spec.name}: derive must be callable")
        if not isinstance(self.decide, Predicate):
            raise TypeError(f"{self.spec.name}: decide must be a Predicate, got {self.decide!r}")
        examples = tuple(self.examples)
        for e in examples:
            if not isinstance(e, Example):
                raise TypeError(f"{self.spec.name}: examples must hold Example, got {e!r}")
        object.__setattr__(self, "examples", examples)
        names = tuple(self.fact_names)
        for n in names:
            if not isinstance(n, str):
                raise TypeError(f"{self.spec.name}: fact_names must hold str, got {n!r}")
        object.__setattr__(self, "fact_names", names)
        modes = [
            m
            for m, v in (
                ("plan", self.plan),
                ("catalog_facts", self.catalog_facts),
                ("unprobeable_reason", self.unprobeable_reason),
            )
            if v is not None
        ]
        if len(modes) != 1:
            got = ", ".join(modes) if modes else "none"
            raise ValueError(
                f"{self.spec.name}: a contract needs exactly one of plan, catalog_facts, "
                f"unprobeable_reason (got {got})"
            )
        if self.unprobeable_reason is not None and (
            not isinstance(self.unprobeable_reason, str) or not self.unprobeable_reason.strip()
        ):
            raise ValueError(f"{self.spec.name}: unprobeable_reason must be a non-empty string")
        for name, fn in (("plan", self.plan), ("catalog_facts", self.catalog_facts)):
            if fn is not None and not callable(fn):
                raise TypeError(f"{self.spec.name}: {name} must be callable")

    @property
    def name(self) -> str:
        return self.spec.name


def _is_probe_plan(plan: object) -> bool:
    """A core primitive, or an instance of a class registered with ``register_primitive``."""
    return isinstance(plan, CORE_PRIMITIVES) or type(plan) in PRIMITIVE_LOWERINGS


def plan_for(contract: Contract, claim: CheckedClaim) -> ProbePlan:
    """The contract's plan for ``claim``. A plan that returns no probe plan (``None``, or an
    object that is neither a core primitive nor a registered one), a sampled plan (FL404) and
    a filterable verb whose plan drops the claim's ``WHERE`` filter (FL605; for
    ``KeyOverlap`` the left ``filter``) are contract bugs and raise ``ValueError``."""
    if contract.plan is None:
        raise ValueError(f"contract {contract.name} has no plan (it is catalog or unprobeable)")
    plan = contract.plan(claim)
    if not _is_probe_plan(plan):
        returned = "None" if plan is None else type(plan).__name__
        raise ValueError(
            f"verb {contract.name}: its plan returned {returned}, not a probe plan; return a "
            "core primitive or an instance of a class registered with "
            "factlang.ir.register_primitive"
        )
    if is_sampled(plan):
        raise ValueError(
            f"FL404 sampled plan in a claim: contract {contract.name} returned a plan with "
            "sample set; claims and contracts are always measured exactly"
        )
    if (
        claim.filter is not None
        and contract.spec.filterable
        and getattr(plan, "filter", None) is None
    ):
        raise ValueError(
            f"FL605 filterable verb {contract.name} dropped the claim's WHERE filter: its "
            "plan has filter=None, so it would measure the whole table; pass "
            "filter=claim.filter on"
        )
    return plan


class ContractBuilder:
    """Built by ``ContractPack.verb``; registers into the pack on its first read."""

    def __init__(self, pack: ContractPack, spec: VerbSpec) -> None:
        self._pack = pack
        self._spec = spec
        self._plan: Plan | None = None
        self._catalog: CatalogFacts | None = None
        self._reason: str | None = None
        self._derive: Derive | None = None
        self._decide: Predicate | None = None
        self._examples: list[Example] = []
        self._facts: tuple[str, ...] = ()
        self._registered = False

    @property
    def name(self) -> str:
        return self._spec.name

    def _open(self) -> None:
        if self._registered:
            raise RuntimeError(f"{self.name}: the contract is already registered; it is frozen")

    def _has_mode(self) -> bool:
        return self._plan is not None or self._catalog is not None or self._reason is not None

    def _set_mode(self) -> None:
        self._open()
        if self._has_mode():
            raise ValueError(
                f"{self.name}: the measurement mode (plan, catalog or unprobeable) is already set"
            )

    def __call__(self, arg: object) -> object:
        """As a decorator: set the plan. Once a plan is set: call it on a claim."""
        if self._plan is None and not self._has_mode() and callable(arg):
            self._set_mode()
            self._plan = arg
            return self
        if self._plan is None:
            raise TypeError(f"{self.name}: this contract has no plan to call")
        return self._plan(arg)  # type: ignore[arg-type]

    def catalog(self, fn: CatalogFacts) -> CatalogFacts:
        self._set_mode()
        self._catalog = fn
        return fn

    def unprobeable(self, reason: str) -> ContractBuilder:
        self._set_mode()
        self._reason = reason
        return self

    def derive(self, fn: Derive) -> Derive:
        self._open()
        self._derive = fn
        return fn

    def decide(self, predicate: Predicate) -> ContractBuilder:
        self._open()
        self._decide = predicate
        return self

    def example(
        self, source: str, valid: bool = True, expect: str | None = None
    ) -> ContractBuilder:
        self._open()
        self._examples.append(Example(source, valid, expect))
        return self

    def facts(self, *names: str) -> ContractBuilder:
        self._open()
        self._facts = tuple(names)
        return self

    def build(self) -> Contract:
        """The finished contract; ``ValueError`` naming the first missing part."""
        if not self._has_mode():
            raise ValueError(
                f"verb {self.name} is incomplete: no plan (decorate the plan function, "
                "or call .catalog(fn) / .unprobeable(reason))"
            )
        if self._derive is None:
            raise ValueError(f"verb {self.name} is incomplete: no derive (use .derive(fn))")
        if self._decide is None:
            raise ValueError(f"verb {self.name} is incomplete: no decide (use .decide(pred))")
        return Contract(
            spec=self._spec,
            derive=self._derive,
            decide=self._decide,
            examples=tuple(self._examples),
            plan=self._plan,
            catalog_facts=self._catalog,
            unprobeable_reason=self._reason,
            fact_names=self._facts,
        )


class ContractPack:
    """A named, versioned set of contracts (spec 10).

    A child pack from ``extend`` resolves names it does not hold in its parent at lookup
    time; its own writes (register, override, alias, remove) never touch the parent.
    """

    def __init__(self, name: str, version: str, *, parent: ContractPack | None = None) -> None:
        self.name = name
        self.version = version
        self._parent = parent
        self._own: dict[str, Contract] = {}
        self._aliases: dict[str, str] = {}
        self._hidden: set[str] = set()  # parent names removed in this pack
        self._pending: dict[str, ContractBuilder] = {}
        self._alias_cache: dict[str, tuple[Contract, Contract]] = {}
        self.overridden: dict[str, Contract] = {}

    # -- resolution ------------------------------------------------------------------------

    def _flush_lenient(self) -> dict[str, str]:
        """Register every complete pending builder, parent chain first.

        Returns ``{verb: why}`` for the builders that are still incomplete (they stay pending).
        """
        errors = {} if self._parent is None else self._parent._flush_lenient()
        for name, builder in list(self._pending.items()):
            try:
                contract = builder.build()
            except ValueError as exc:
                errors[name] = str(exc)
                continue
            del self._pending[name]
            builder._registered = True
            self._own[name] = contract
        return errors

    def _flush(self) -> None:
        """Like ``_flush_lenient`` but raises ``ValueError`` naming the first missing part."""
        errors = self._flush_lenient()
        if errors:
            raise ValueError(next(iter(errors.values())))

    def _pending_visible(self, name: str) -> bool:
        if name in self._pending:
            return True
        if name in self._own or name in self._aliases or name in self._hidden:
            return False
        return self._parent is not None and self._parent._pending_visible(name)

    def _alias_target(self, name: str) -> str | None:
        """Target name when ``name`` is an alias visible from this pack."""
        if name in self._aliases:
            return self._aliases[name]
        if name in self._own or name in self._hidden or self._parent is None:
            return None
        return self._parent._alias_target(name)

    def _as_alias(self, name: str, target: Contract) -> Contract:
        """``target`` under the alias ``name`` (spec and examples renamed), cached per target."""
        hit = self._alias_cache.get(name)
        if hit is not None and hit[0] is target:
            return hit[1]
        examples = tuple(
            replace(e, source=_rename_verb(e.source, target.name, name)) for e in target.examples
        )
        clone = replace(target, spec=replace(target.spec, name=name), examples=examples)
        self._alias_cache[name] = (target, clone)
        return clone

    def _resolve(self, name: str, seen: frozenset[str] = frozenset()) -> Contract | None:
        """Lookup without flushing pending builders."""
        if name in seen:
            return None
        # an alias (own or inherited) resolves its target from this pack, so it follows
        # overrides made here
        target_name = self._alias_target(name)
        if target_name is not None:
            target = self._resolve(target_name, seen | {name})
            return None if target is None else self._as_alias(name, target)
        if name in self._own:
            return self._own[name]
        if name in self._hidden or self._parent is None:
            return None
        return self._parent._resolve(name, seen)

    def _all_names(self) -> set[str]:
        names = set(self._own) | set(self._aliases)
        if self._parent is not None:
            names |= self._parent._all_names() - self._hidden
        return names

    def _names(self) -> list[str]:
        return sorted(n for n in self._all_names() if self._resolve(n) is not None)

    def _registry(self) -> VerbRegistry:
        registry = VerbRegistry()
        for n in self._names():
            c = self._resolve(n)
            if c is not None:
                registry.register(c.spec)
        return registry

    def get(self, name: str) -> Contract | None:
        self._flush()
        return self._resolve(name)

    def names(self) -> list[str]:
        self._flush()
        return self._names()

    def contracts(self) -> list[Contract]:
        self._flush()
        out: list[Contract] = []
        for n in self._names():
            c = self._resolve(n)
            if c is not None:
                out.append(c)
        return out

    def is_alias(self, name: str) -> bool:
        self._flush()
        return self._alias_target(name) is not None

    def verb_registry(self) -> VerbRegistry:
        self._flush()
        return self._registry()

    # -- writes ----------------------------------------------------------------------------

    def _taken(self, name: str) -> bool:
        return self._pending_visible(name) or self._resolve(name) is not None

    def _drop(self, name: str) -> None:
        self._own.pop(name, None)
        self._aliases.pop(name, None)
        self._hidden.discard(name)

    def register(self, contract: Contract) -> None:
        if self._taken(contract.name):
            raise ValueError(
                f"verb {contract.name} is already in pack {self.name}; use override() to replace"
            )
        self._drop(contract.name)
        self._own[contract.name] = contract

    def override(self, contract: Contract) -> None:
        previous = self.get(contract.name)
        if previous is None:
            raise ValueError(
                f"verb {contract.name} is not in pack {self.name}; use register() to add it"
            )
        self.overridden[contract.name] = previous
        self._drop(contract.name)
        self._own[contract.name] = contract

    def alias(self, new: str, target: str) -> None:
        VerbSpec(new, "", ())  # validates the name (upper-case)
        self._flush()
        if self._taken(new):
            raise ValueError(f"verb {new} is already in pack {self.name}")
        if self._resolve(target) is None:
            raise ValueError(f"alias {new}: no verb {target} in pack {self.name}")
        self._drop(new)
        self._aliases[new] = target

    def remove(self, name: str) -> None:
        self._flush()
        if name not in self._all_names():
            raise KeyError(f"no verb {name} in pack {self.name}")
        self._drop(name)
        if self._parent is not None and name in self._parent._all_names():
            self._hidden.add(name)

    def extend(self, name: str, version: str) -> ContractPack:
        return ContractPack(name, version, parent=self)

    def merge(self, other: ContractPack, on_conflict: OnConflict = "error") -> ContractPack:
        """Add ``other``'s verbs (aliases stay aliases). ``error`` is atomic."""
        if on_conflict not in ("error", "keep", "replace"):
            raise ValueError(f"on_conflict must be error, keep or replace, got {on_conflict!r}")
        self._flush()
        names = other.names()
        clashes = [n for n in names if self._taken(n)]
        if clashes and on_conflict == "error":
            raise ValueError(f"merge into {self.name}: verbs already present: {', '.join(clashes)}")
        concrete = [n for n in names if not other.is_alias(n)]
        aliases = [n for n in names if other.is_alias(n)]
        for n in concrete + aliases:
            if n in clashes and on_conflict == "keep":
                continue
            target = other._alias_target(n)
            if target is not None and self._resolve(target) is not None and n not in clashes:
                self._drop(n)
                self._aliases[n] = target
                continue
            contract = other.get(n)
            if contract is None:
                continue
            if n in clashes:
                self.override(contract)
            else:
                self.register(contract)
        return self

    # -- decorator API ---------------------------------------------------------------------

    def verb(
        self,
        name: str,
        *,
        summary: str = "",
        operands: Sequence[OperandSpec],
        attrs: Iterable[AttrSpec] = (),
        filterable: bool = False,
        requires: Iterable[tuple[str, str]] = (),
    ) -> ContractBuilder:
        spec = VerbSpec(
            name,
            summary,
            tuple(operands),
            attrs=tuple(attrs),
            filterable=filterable,
            requires=tuple(requires),
        )
        if self._taken(name):
            raise ValueError(f"verb {name} is already in pack {self.name}")
        builder = ContractBuilder(self, spec)
        self._pending[name] = builder
        return builder


def discover_packs() -> dict[str, Callable[[], ContractPack]]:
    """Pack factories from the ``factlang.packs`` entry-point group; loaded on first call."""

    def factory(ep: object) -> Callable[[], ContractPack]:
        def load() -> ContractPack:
            pack = ep.load()()  # type: ignore[attr-defined]
            if not isinstance(pack, ContractPack):
                name = ep.name  # type: ignore[attr-defined]
                raise TypeError(f"entry point {name!r} did not return a ContractPack: {pack!r}")
            return pack

        return load

    return {ep.name: factory(ep) for ep in _entry_points(group=PACK_ENTRY_POINT_GROUP)}


# -- completeness (FL6xx, FL404) -----------------------------------------------------------


def _check_example(contract: Contract, ex: Example, registry: VerbRegistry) -> list[str]:
    where = f"{contract.name}: example {ex.source!r}"
    lead = _LEADING_WORD.match(ex.source)
    if lead is None or lead.group(1) != contract.name:
        return [f"FL603: {where} does not start with its own verb {contract.name}"]
    parsed = parse(ex.source)
    bag = DiagnosticBag()
    bag.extend(parsed.diagnostics)
    claims = analyze(parsed.document, registry, catalog=NullCatalog(), bag=bag)
    errors = [d.code for d in bag if d.severity is Severity.ERROR]
    if not ex.valid:
        if not errors:
            return [f"FL603: {where} is declared invalid ({ex.expect}) but analyses cleanly"]
        if ex.expect not in errors:
            return [f"FL603: {where} expects {ex.expect} but raised {', '.join(errors)}"]
        return []
    if errors:
        return [f"FL603: {where} is declared valid but raised {', '.join(errors)}"]
    if len(claims) != 1:
        return [f"FL603: {where} must hold exactly one claim, got {len(claims)}"]
    claim = claims[0]
    if claim.verb != contract.name:
        return [f"FL603: {where} is a {claim.verb} claim, not {contract.name}"]
    if ex.expect is not None and ex.expect not in bag.codes():
        return [f"FL603: {where} expects diagnostic {ex.expect}, got {bag.codes()}"]
    try:
        if contract.plan is not None:
            plan_for(contract, claim)
        elif contract.catalog_facts is not None:
            contract.catalog_facts(claim, NullCatalog())
    except ValueError as exc:
        for code in ("FL404", "FL605"):
            if str(exc).startswith(code):
                return [f"{code}: {where}: {exc}"]
        return [f"FL603: {where}: planning raised ValueError: {exc}"]
    except Exception as exc:
        return [f"FL603: {where}: planning raised {type(exc).__name__}: {exc}"]
    return []


def check_contract(contract: Contract, registry: VerbRegistry) -> list[str]:
    """Completeness problems of one contract, each prefixed with its code."""
    out: list[str] = []
    facts, attrs = contract.decide.reads()
    missing_facts = sorted(facts - set(contract.fact_names))
    if missing_facts:
        out.append(
            f"FL600: {contract.name}: decide reads fact(s) "
            f"{', '.join(repr(f) for f in missing_facts)} that derive never produces "
            f"(fact_names: {list(contract.fact_names)})"
        )
    declared = {a.name for a in contract.spec.attrs}
    missing_attrs = sorted(attrs - declared)
    if missing_attrs:
        out.append(
            f"FL601: {contract.name}: decide reads undeclared attribute(s) "
            f"{', '.join(repr(a) for a in missing_attrs)}"
        )
    valid = sum(1 for e in contract.examples if e.valid)
    invalid = len(contract.examples) - valid
    if valid < MIN_VALID_EXAMPLES or invalid < MIN_INVALID_EXAMPLES:
        out.append(
            f"FL602: {contract.name}: needs >= {MIN_VALID_EXAMPLES} valid and >= "
            f"{MIN_INVALID_EXAMPLES} invalid examples, has {valid} valid and {invalid} invalid"
        )
    for ex in contract.examples:
        out.extend(_check_example(contract, ex, registry))
    return out


def check_pack(pack: ContractPack) -> list[str]:
    """Every completeness problem of ``pack`` (incl. inherited verbs), ``[]`` if none.

    Codes: FL600/FL601 (decide reads undeclared facts/attributes), FL602 (too few
    examples), FL603 (an example does not behave as declared, or its plan/catalog function
    raises), FL604 (an incomplete decorator-built contract; reported, never raised, and
    the other contracts are still checked), FL404 (a sampled plan), FL605 (a valid example with
    a ``WHERE`` whose plan drops the filter). Aliases are checked
    through their target.
    """
    incomplete = pack._flush_lenient()
    out = [f"FL604: {name}: incomplete contract: {why}" for name, why in incomplete.items()]
    registry = pack._registry()
    for name in pack._names():
        if pack._alias_target(name) is not None:
            continue
        contract = pack._resolve(name)
        if contract is not None:
            out.extend(check_contract(contract, registry))
    return out


__all__ = [
    "MIN_INVALID_EXAMPLES",
    "MIN_VALID_EXAMPLES",
    "PACK_ENTRY_POINT_GROUP",
    "CatalogFacts",
    "Contract",
    "ContractBuilder",
    "ContractPack",
    "Example",
    "OnConflict",
    "Plan",
    "check_contract",
    "check_pack",
    "discover_packs",
    "plan_for",
]
