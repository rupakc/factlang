# Value types

Every attribute has a **value type** that says which literal kinds it accepts and how a
literal becomes a Python value. The built-in types include `NUMBER`, `INTEGER`, `RATIO`
(percentages normalised, 0 to 1), `DURATION`, `STRING`, `SYMBOL`, `TYPE_FAMILY`,
`CARDINALITY`, `COLUMN_LIST`, `VALUE_LIST`, `RANGE_PAIR`, `SEPARATOR` and `LIKE_PATTERN`.
Every code block on this page runs in the test suite.

A `ValueType` has four parts:

| field | |
|---|---|
| `name` | uppercase, used in `fl.Attr(name, "<TYPE>")` |
| `accepts` | the literal kinds allowed; anything else is `FL305` |
| `coerce(literal)` | turns the literal into a value; returns `fl.types.Invalid(message)` instead of raising |
| `validate(value)` | returns an error message (reported as `FL309`) or `None` |

## A custom type

```python
import factlang as fl
LiteralKind = fl.types.LiteralKind

def domain(literal):
    text = str(literal.value).lower()
    return text if "." in text else fl.types.Invalid(f"{text!r} is not a domain name")

fl.types.register(
    fl.types.ValueType("EMAIL_DOMAIN", frozenset({LiteralKind.STRING}), coerce=domain),
    replace=True,
)

pack = fl.standard_pack().extend("mail", "1.0")

@pack.verb(
    "MAIL_DOMAIN",
    summary="Every e-mail address of the column ends with the domain.",
    operands=[fl.Operand.column()],
    attrs=[fl.Attr("IS", "EMAIL_DOMAIN", required=True)],
)
def mail_domain(claim):
    pattern = "%@" + claim.value("IS")
    return fl.ir.PatternCoverage(claim.table, claim.column.column, pattern)

@mail_domain.derive
def _(payload, claim):
    return {"match_rate": None if not payload["non_null"] else payload["matching"] / payload["non_null"]}

mail_domain.facts("match_rate").decide(fl.Compare("match_rate", "==", value=1))

assert fl.lint("MAIL_DOMAIN customers.email IS 'example.com'", pack=pack).codes() == []
assert fl.lint("MAIL_DOMAIN customers.email IS 'localhost'", pack=pack).codes() == ["FL309"]
assert fl.lint("MAIL_DOMAIN customers.email IS 42", pack=pack).codes() == ["FL305"]
```

The registry is process-wide. Registering an existing name raises unless `replace=True`.

## See also

- [New verbs](verbs.md)
- [The language: values](../guide/language.md#values)
