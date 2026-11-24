# Packs

A **pack** is a named, versioned set of contracts. The standard pack is `standard` version
`1.0`; your own verbs live in a pack that extends it. Every code block on this page runs in
the test suite.

```python
import factlang as fl

pack = fl.standard_pack().extend("mine", "1.0")   # copy-on-write child: standard is untouched
pack.alias("CNT", "COUNT")                       # a second name for a verb
pack.remove("DEAD")                              # hide a verb in this pack only

assert fl.lint("CNT orders VALUE >=1", pack=pack).codes() == []
assert fl.lint("DEAD orders.legacy_flag", pack=pack).codes() == ["FL300"]
assert fl.lint("DEAD orders.legacy_flag").codes() == []   # the standard pack still has it
```

## Operations

| operation | does |
|---|---|
| `register(contract)` | adds a `Contract`; raises if the name is taken |
| `override(contract)` | replaces a verb and keeps the replaced one in `pack.overridden[name]` |
| `alias(new, target)` | adds a second name; the alias analyses and hashes as its target |
| `remove(name)` | removes or hides a verb |
| `extend(name, version)` | a child pack that sees the parent's verbs and can change them without touching the parent |
| `merge(other, on_conflict="error" \| "keep" \| "replace")` | adds another pack's verbs; `error` is atomic |
| `verb(name, …)` | the decorator API of [New verbs](verbs.md) |
| `get(name)`, `names()`, `contracts()` | inspection |

Overriding is how you change a standard verb's behaviour, for example to tighten what
`UNIQUE` reports, while keeping every other verb:

```python
from dataclasses import replace

strict = fl.standard_pack().extend("strict", "1.0")
unique = strict.get("UNIQUE")
strict.override(replace(unique, spec=replace(unique.spec, summary="No key repeats, ever.")))
assert strict.overridden["UNIQUE"] is unique
```

## Distributing a pack

Expose a zero-argument factory under the `factlang.packs` entry-point group in your
package's `pyproject.toml`:

```toml
[project.entry-points."factlang.packs"]
mine = "my_project.factlang_pack:build_pack"
```

`factlang.contract.discover_packs()` returns `{name: factory}` for every installed pack;
calling a factory loads it.

## Testing a pack

```python
from factlang import check_pack

assert check_pack(strict) == []
```

`check_pack` runs every example of every contract and checks the completeness rules
(`FL600`–`FL605`, `FL404`); see [New verbs](verbs.md#rules-the-tests-enforce).

## See also

- [New verbs](verbs.md)
- [Value types](types.md)
