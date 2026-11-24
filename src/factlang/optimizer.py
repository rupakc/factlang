"""Probe de-duplication and fusion (spec 9.3; layer 13).

``optimize`` de-duplicates work items by key (a claim's ``semantic_hash``, an explicit
``key``, or ``canonical_key(plan)`` for a raw measurement) and fuses single-read aggregate
plans over one table into one :class:`FusedAggregate` probe. ``split`` maps the fused result
rows back to each member's exact wire-format aliases (R1: fused == unfused).

Only plans with the same table, canonical filter and sample are fused, and only plans whose
own lowering is one flat ``SELECT`` over the table (no key grouping), so a fused probe reads
the table exactly once and sampling (FL405) is never bypassed. Everything else stays unfused.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar

from factlang.ir import (
    Arity,
    ColumnAggregate,
    CompositeKeyAggregate,
    ProbePlan,
    SampleSpec,
    canonical_key,
    register_primitive,
)
from factlang.semantics.checked import CheckedClaim
from factlang.semantics.filters import FilterExpr, canonical
from factlang.semantics.refs import TableRef
from factlang.sql.ast import Aliased, Select, TableName, UnionAll
from factlang.sql.lower import Lowerer
from factlang.sql.probe import RenderError

_Member = ColumnAggregate | CompositeKeyAggregate


@dataclass(frozen=True)
class WorkItem:
    """One measurement to run: a claim's plan, or a raw measurement (``claim=None``)."""

    plan: ProbePlan
    claim: CheckedClaim | None = None
    key: str | None = None

    @property
    def resolved_key(self) -> str:
        if self.key is not None:
            return self.key
        if self.claim is not None:
            return self.claim.semantic_hash
        return canonical_key(self.plan)


@dataclass(frozen=True)
class FusedAggregate:
    """Several flat aggregate plans over one table/filter/sample, read once."""

    arity: ClassVar[Arity] = Arity.SINGLE_ROW
    table: TableRef
    members: tuple[_Member, ...]
    filter: FilterExpr | None = None
    sample: SampleSpec | None = None

    def __post_init__(self) -> None:
        if isinstance(self.members, list):
            object.__setattr__(self, "members", tuple(self.members))
        if not self.members:
            raise ValueError("FusedAggregate needs at least one member")

    def result_columns(self) -> tuple[str, ...]:
        return tuple(
            fused_alias(i, a) for i, m in enumerate(self.members) for a in m.result_columns()
        )


def fused_alias(index: int, alias: str) -> str:
    return f"m{index}__{alias}"


def _flat_lowering(plan: _Member) -> Select | None:
    """The plan's own lowering when it is one flat SELECT over the plain table, else None."""
    try:
        tree = Lowerer().lower(plan)
    except RenderError:  # e.g. FL405: stays unfused and reports its own error at render time
        return None
    if isinstance(tree, Select) and isinstance(tree.from_, TableName) and not tree.group_by:
        return tree
    return None


def _lower_fused(plan: FusedAggregate) -> Select | UnionAll:
    trees = [_flat_lowering(m) for m in plan.members]
    first = trees[0]
    if first is None:
        raise RenderError("FL500 FusedAggregate member is not a flat single-read aggregate")
    columns: list[Aliased] = []
    for i, tree in enumerate(trees):
        if tree is None or tree.from_ != first.from_ or tree.where != first.where:
            raise RenderError("FL500 FusedAggregate members do not share one source and filter")
        columns.extend(Aliased(c.expr, fused_alias(i, c.alias)) for c in tree.columns)
    return Select(tuple(columns), first.from_, first.where)


register_primitive(FusedAggregate, lower=_lower_fused, replace=True)


@dataclass(frozen=True)
class FusedProbe:
    """A probe to run and, per member key, its alias -> fused-alias map."""

    plan: object
    members: tuple[tuple[str, Mapping[str, str]], ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "members",
            tuple((k, MappingProxyType(dict(m))) for k, m in self.members),
        )


def _group_key(plan: ProbePlan) -> tuple[object, ...] | None:
    if not isinstance(plan, (ColumnAggregate, CompositeKeyAggregate)):
        return None
    if _flat_lowering(plan) is None:
        return None
    flt = canonical(plan.filter) if plan.filter is not None else None
    cols = plan.columns if isinstance(plan, CompositeKeyAggregate) else None
    return (type(plan).__name__, plan.table.key(), flt, plan.sample, cols)


def _single(key: str, plan: ProbePlan) -> FusedProbe:
    ident = {a: a for a in plan.result_columns()}
    return FusedProbe(plan, ((key, ident),))


def optimize(items: Sequence[WorkItem], *, fuse: bool = True) -> list[FusedProbe]:
    """De-duplicate ``items`` by key and (unless ``fuse=False``) fuse compatible plans."""
    unique: dict[str, ProbePlan] = {}
    for item in items:
        unique.setdefault(item.resolved_key, item.plan)
    slots: list[list[tuple[str, ProbePlan]]] = []
    groups: dict[tuple[object, ...], list[tuple[str, ProbePlan]]] = {}
    for key, plan in unique.items():
        gk = _group_key(plan) if fuse else None
        if gk is None:
            slots.append([(key, plan)])
        elif gk in groups:
            groups[gk].append((key, plan))
        else:
            groups[gk] = [(key, plan)]
            slots.append(groups[gk])
    out: list[FusedProbe] = []
    for slot in slots:
        if len(slot) == 1:
            out.append(_single(*slot[0]))
            continue
        plans = tuple(p for _, p in slot)
        first = plans[0]
        assert isinstance(first, (ColumnAggregate, CompositeKeyAggregate))
        fused = FusedAggregate(first.table, plans, first.filter, first.sample)  # type: ignore[arg-type]
        members = tuple(
            (key, {a: fused_alias(i, a) for a in plan.result_columns()})
            for i, (key, plan) in enumerate(slot)
        )
        out.append(FusedProbe(fused, members))
    return out


def split(
    fused: FusedProbe, rows: Sequence[Mapping[str, object]]
) -> dict[str, list[dict[str, object]]]:
    """Member-shaped rows per member key, with each member's own aliases restored."""
    return {
        key: [{alias: row[fa] for alias, fa in amap.items()} for row in rows]
        for key, amap in fused.members
    }


__all__ = ["FusedAggregate", "FusedProbe", "WorkItem", "fused_alias", "optimize", "split"]
