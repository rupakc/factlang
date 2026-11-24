# Extending Verbs

> A new verb is one contract: its syntax, how to measure it, how to compute facts, how to decide, and examples, registered in a pack.
> The decorator API lets you write one in about ten lines, and the examples you attach become tests.
> You can also override or alias an existing verb, and add new attribute value types.

## A worked example

```python
import factlang as fl

pack = fl.standard_pack().extend("mine", "1.0")   # copy-on-write child pack

@pack.verb("POSITIVE", operands=[fl.Operand.column()], filterable=True)
def positive(claim):
    # return the plan: what to measure
    return fl.ir.ColumnAggregate(claim.table, claim.column, [fl.Metric.MIN_VALUE],
                                 filter=claim.filter)   # a filterable verb must pass its filter on

@positive.derive
def _(payload, claim):
    return {"min": payload["min_value"]}               # facts; None when undecidable

positive.decide(fl.Compare("min", ">", value=0))        # a predicate over the facts

positive.example("POSITIVE orders.amount", valid=True)
positive.example("POSITIVE orders", valid=False, code="FL302")
# a contract needs at least 3 valid and 3 invalid examples
```

Use it with `fl.Checker(connector, pack=pack)`.

## The parts of a contract

| Part | Question it answers |
|---|---|
| spec (`operands`, `attrs`, `filterable`, `requires`) | what does the syntax look like? |
| plan | which primitives must be measured? |
| derive | which facts come from the rows? Return `None` for undecidable. |
| decide | when is the claim true? A predicate over facts. |
| examples | what valid and invalid lines prove it works? |

A verb is exactly one of: **probeable** (has a plan), **catalog-only** (sets `catalog_facts`), or **unprobeable** (gives a reason).

## Completeness checks

Tests run over every contract in every installed pack: decide reads only facts derive produces (FL600) and only declared attributes (FL601); at least three valid and three invalid examples (FL602); every example behaves as declared (FL603); the decorator builder is complete (FL604); a filterable verb's plan passes the claim's filter on (FL605).

## Overriding, aliasing, removing

On a `ContractPack`: `register(c)` (error on duplicate), `override(c)` (replace, remembering what it replaced), `alias("CNT", "COUNT")`, `remove(name)`. See [[Extending-Packs]].

## New value types

```python
fl.types.register(ValueType("EMAIL_DOMAIN",
    accepts=frozenset({LiteralKind.STRING}), coerce=fn, validate=check))
```

## New primitives

If no existing primitive measures what you need, register one with `fl.ir.register_primitive(cls, lower=fn)`; see [[IR]].

## Testing your verb

Run the completeness and example tests over your pack, and add an execution test on DuckDB with a tiny table that gives a PASS, a FAIL and a NO_DATA case.

## See also

- [[Extending-Packs]]
- [[Standard-Verbs]]
- [[Judge]]
- [[IR]]
- [[Contributing]]
