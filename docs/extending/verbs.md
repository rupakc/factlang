# New verbs

A verb is data, not grammar: the parser accepts `VERB operands ATTRIBUTES [WHERE …]` for any
uppercase verb, and the analyser checks the line against the verb's signature. Adding a verb
means writing one **contract**:

| part | job |
|---|---|
| signature | operands, attributes (with value types and allowed operators), whether `WHERE` is accepted |
| plan | turns a checked claim into an [IR primitive](primitives.md): what to measure |
| derive | turns the measured row(s) into named facts; returns `None` for a fact the data cannot support |
| decide | a predicate over the facts: the verdict |
| examples | at least three valid and three invalid claims, executed by the tests |

Every code block on this page runs against DuckDB in the test suite.

## A verb in a dozen lines

`POSITIVE t.c` claims that every non-NULL value of a column is above zero.

```python
import duckdb
import factlang as fl

pack = fl.standard_pack().extend("mine", "1.0")

@pack.verb(
    "POSITIVE",
    summary="Every non-NULL value of the column is greater than zero.",
    operands=[fl.Operand.column()],
    filterable=True,
)
def positive(claim):
    return fl.ir.ColumnAggregate(
        claim.table, claim.column, [fl.Metric.MIN_VALUE], filter=claim.filter
    )

@positive.derive
def _(payload, claim):
    return {"min": payload["min_value"]}

positive.facts("min")
positive.decide(fl.Compare("min", ">", value=0))
positive.example("POSITIVE orders.amount")
positive.example("POSITIVE order_items.price WHERE status = 'paid'")
positive.example("POSITIVE shop.orders.amount")
positive.example("POSITIVE orders", valid=False, expect="FL302")
positive.example("POSITIVE orders.amount VALUE > 0", valid=False, expect="FL303")
positive.example("POSITIVE orders.amount WHERE", valid=False, expect="FL206")
```

Use it by passing the pack to a `Checker` (or to `fl.lint` / `fl.compile`):

```python
conn = duckdb.connect()
conn.execute("CREATE TABLE orders (id INTEGER, amount DECIMAL(10, 2), status VARCHAR)")
conn.execute("INSERT INTO orders VALUES (1, 5.00, 'paid'), (2, -1.00, 'void'), (3, NULL, 'open')")
checker = fl.Checker(fl.DBAPIConnector(conn, "duckdb"), pack=pack)

report = checker.check("POSITIVE orders.amount\nPOSITIVE orders.amount WHERE status = 'paid'")
for o in report.outcomes:
    print(o.verdict.value, o.explanation)
# fail min -1 <= 0
# pass min 5 > 0
assert [o.verdict.value for o in report.outcomes] == ["fail", "pass"]
```

### Rules the tests enforce

Run `fl.check_pack(pack)` in your own test suite; it returns a list of
problems, empty when the pack is complete:

```python
from factlang import check_pack

assert check_pack(pack) == []
```

It reports:

- `FL600`: `decide` reads a fact that `derive` never declares (`.facts(...)`).
- `FL601`: `decide` reads an attribute the signature does not declare.
- `FL602`: fewer than three valid and three invalid examples.
- `FL603`: an example does not behave as declared (or its plan raises).
- `FL604`: a decorator-built contract is missing a part (plan, derive or decide).
- `FL605`: a **filterable** verb's plan drops the claim's `WHERE` filter. A plan that ignores
  `claim.filter` would measure the whole table while the claim talks about some of it, so
  always pass `filter=claim.filter` on.
- `FL404`: the plan sets `sample`. Claims are always measured exactly.

## Attributes and predicates

Attributes are declared with `fl.Attr(name, type, required=False, ops=(), doc="")`, where
`type` names a [value type](types.md) (`NUMBER`, `INTEGER`, `RATIO`, `DURATION`, `STRING`,
`SYMBOL`, `COLUMN_LIST`, …) and `ops` lists the operators the attribute accepts (empty: it
takes none). The predicate language is small on purpose, so a verdict always explains itself:

| predicate | decides |
|---|---|
| `Compare(fact, op, value=…)` | a fact against a constant |
| `Compare(fact, op, attr="NAME")` | a fact against an attribute of the claim, with a fixed operator |
| `CompareUsingClaimOp(fact, "NAME")` | a fact against the attribute, using the operator the author wrote |
| `Within(fact, "VALUE")` | like the above, with the claim's `TOLERANCE` / `ABS_TOLERANCE` band |
| `And(...)`, `Or(...)`, `Not(p)` | three-valued combinations: `None` stays undecidable |
| `WhenClaimHas("NAME", p)` | applies `p` only when the claim carries the attribute |

```python
@pack.verb(
    "LOWEST",
    summary="The smallest non-NULL value of the column satisfies the stated comparison.",
    operands=[fl.Operand.column()],
    attrs=[fl.Attr("VALUE", "NUMBER", required=True, ops=("==", "<=", "<", ">=", ">"))],
    filterable=True,
)
def lowest(claim):
    return fl.ir.ColumnAggregate(
        claim.table, claim.column, [fl.Metric.MIN_VALUE], filter=claim.filter
    )

@lowest.derive
def _(payload, claim):
    return {"lowest": payload["min_value"]}  # None over no rows: the verdict is no_data

lowest.facts("lowest")
lowest.decide(fl.CompareUsingClaimOp("lowest", "VALUE"))
lowest.example("LOWEST orders.amount VALUE >= 0")
lowest.example("LOWEST orders.amount VALUE == 5 WHERE status = 'paid'")
lowest.example("LOWEST shop.orders.amount VALUE < 100")
lowest.example("LOWEST orders.amount", valid=False, expect="FL304")
lowest.example("LOWEST orders.amount VALUE 0", valid=False, expect="FL306")
lowest.example("LOWEST orders.amount VALUE >= 'x'", valid=False, expect="FL305")

report = checker.check("LOWEST orders.amount VALUE >= -1\nLOWEST orders.amount VALUE >= 0")
assert [o.verdict.value for o in report.outcomes] == ["pass", "fail"]
assert check_pack(pack) == []
```

Writing a contract as a `Contract(...)` object instead of with the decorator gives the same
result; the [standard verbs](https://github.com/rupakc/factlang/tree/main/src/factlang/packs/standard)
are written that way, one file per verb, and make good templates.

## Catalog and semantic verbs

A verb answered from the catalog uses `.catalog(fn)` instead of a plan (`fn(claim, catalog)`
returns the facts); a verb no query can establish uses `.unprobeable("reason")` and is
reported as `unprobeable`, like `NOTE`. Exactly one of plan, catalog and unprobeable is set.

## Overriding and aliasing

See [Packs](packs.md): `override` replaces a verb and records the replaced version, `alias`
adds a second name, and packs are discoverable through an entry point.

## See also

- [IR primitives](primitives.md): what a plan can measure.
- [Value types](types.md): custom attribute types.
- [Verb reference](../reference/verbs.md)
