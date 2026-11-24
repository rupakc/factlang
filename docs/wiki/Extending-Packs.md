# Extending Packs

> A pack is a named, versioned collection of verb contracts. Build your own by extending the standard pack or starting empty.
> Packs are copy-on-write, so extending never changes the original, and they can be discovered through an entry point.
> Use packs to add house verbs, rename verbs for your team, or restrict what an agent may say.

## Operations on a `ContractPack`

| Operation | Effect |
|---|---|
| `register(contract)` | add a verb; raises on a duplicate |
| `override(contract)` | replace a verb and record the version it replaced |
| `alias("CNT", "COUNT")` | a second name for an existing verb |
| `remove(name)` | drop a verb (for example to forbid it for an agent) |
| `extend(name, version)` | new child pack, copy-on-write |
| `merge(other, on_conflict="error" / "keep" / "replace")` | combine two packs |

```python
pack = fl.standard_pack().extend("acme", "1.0")
pack.alias("CNT", "COUNT")
pack.remove("NOTE")
checker = fl.Checker(connector, pack=pack)
```

## Publishing a pack

Expose a factory function and register it under the entry-point group `factlang.packs` in your `pyproject.toml`; FactLang discovers installed packs automatically.

## Pack checks

Every pack passes through the completeness checks (FL600 to FL605) in tests. Run them over your pack before you ship it. See [[Extending-Verbs]].

## Agents and packs

The tool schema and the verb reference are generated from the active pack, so a restricted pack gives the model a restricted vocabulary. See [[Agent-Integration]].

## See also

- [[Extending-Verbs]]
- [[Standard-Verbs]]
- [[Agent-Integration]]
- [[Contributing]]
