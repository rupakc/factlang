"""The prompt-ready verb reference (spec 10, agent surface; layer 17).

Everything in it is generated from the pack: each contract's ``VerbSpec`` (name, summary,
operands, attributes, operators, constraints, ``WHERE`` support) and its *valid* examples
(which the contract-completeness tests execute). Nothing is written by hand, so the
reference cannot drift from what the analyser accepts.

``style="markdown"`` is a full reference for documentation and system prompts;
``style="compact"`` is a few lines per verb, small enough to sit in one tool description.
"""

from __future__ import annotations

from typing import Literal

from factlang.contract import Contract, ContractPack
from factlang.packs.standard import standard_pack
from factlang.semantics.signature import AttrSpec, OperandKind, OperandSpec, VerbSpec

Style = Literal["markdown", "compact"]

# examples per verb in the compact style (the markdown style lists every valid example)
COMPACT_EXAMPLES = 2


def _operand(o: OperandSpec) -> str:
    if o.kind is OperandKind.TABLE:
        return f"<{o.name}>"
    if o.kind is OperandKind.COLUMN:
        return f"<table.{o.name}>"
    return "<table.column> -> <table.column>"


def _attr(a: AttrSpec) -> str:
    ops = f"{'|'.join(a.ops)} " if a.ops else ""
    text = f"{a.name} {ops}<{a.type}>"
    return text if a.required else f"[{text}]"


def syntax(spec: VerbSpec) -> str:
    """One line: ``VERB <operands> ATTR op <TYPE> [OPTIONAL <TYPE>] [WHERE <condition>]``."""
    parts = [spec.name, *(_operand(o) for o in spec.operands), *(_attr(a) for a in spec.attrs)]
    if spec.filterable:
        parts.append("[WHERE <condition>]")
    return " ".join(parts)


def _constraints(spec: VerbSpec) -> list[str]:
    out = [f"`{a}` requires `{b}`" for a, b in spec.requires]
    out += [f"`{a}` and `{b}` are mutually exclusive" for a, b in spec.exclusive]
    out += [f"`{a}` and `{b}` go together and list as many columns" for a, b in spec.paired]
    return out


def _valid_examples(contract: Contract) -> list[str]:
    return [e.source for e in contract.examples if e.valid]


def _header(pack: ContractPack) -> str:
    return f"FactLang verbs (pack {pack.name} {pack.version})"


def _compact(pack: ContractPack) -> str:
    lines = [
        _header(pack) + ". One claim per line; [..] is optional; <TYPE> is a value; "
        "a|b are the allowed operators.",
    ]
    for contract in pack.contracts():
        spec = contract.spec
        lines.append(syntax(spec))
        if spec.summary:
            lines.append(f"  {spec.summary}")
        lines.extend(f"  e.g. {src}" for src in _valid_examples(contract)[:COMPACT_EXAMPLES])
    return "\n".join(lines) + "\n"


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def _markdown(pack: ContractPack) -> str:
    lines = [f"# {_header(pack)}", ""]
    for contract in pack.contracts():
        spec = contract.spec
        lines += [f"## {spec.name}", ""]
        if spec.summary:
            lines += [spec.summary, ""]
        lines += ["```", syntax(spec), "```", ""]
        if spec.attrs:
            lines += [
                "| attribute | value type | operators | required | meaning |",
                "|---|---|---|---|---|",
            ]
            for a in spec.attrs:
                ops = " ".join(f"`{_cell(op)}`" for op in a.ops) if a.ops else "none"
                lines.append(
                    f"| `{a.name}` | `{a.type}` | {ops} | {'yes' if a.required else 'no'} "
                    f"| {_cell(a.doc)} |"
                )
            lines.append("")
        notes = _constraints(spec)
        notes.append(
            "Accepts a `WHERE <condition>` filter."
            if spec.filterable
            else "Does not accept a `WHERE` filter."
        )
        lines += [f"- {n}" for n in notes] + [""]
        examples = _valid_examples(contract)
        if examples:
            lines += ["Examples:", "", "```", *examples, "```", ""]
    return "\n".join(lines).rstrip("\n") + "\n"


def verb_reference(pack: ContractPack | None = None, style: Style = "markdown") -> str:
    """The reference of every verb in ``pack`` (default: the standard pack)."""
    if style not in ("markdown", "compact"):
        raise ValueError(f"style must be 'markdown' or 'compact', got {style!r}")
    pack = pack if pack is not None else standard_pack()
    return _compact(pack) if style == "compact" else _markdown(pack)


__all__ = ["Style", "syntax", "verb_reference"]
