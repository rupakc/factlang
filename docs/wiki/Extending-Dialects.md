# Extending Dialects

> A new dialect is a `DialectProfile`: a frozen record of small decisions that you register by name.
> Start from an existing profile with `dataclasses.replace` and change only what differs, then run the shared conformance suite.
> If you only need a one-off warehouse, the sqlglot bridge may already cover it.

## Do you need a profile?

If sqlglot supports your warehouse, `dialect="name"` already works as `best_effort`. Write a native profile when you want the verified or native tier, exact control of NULL and quoting behaviour, or sampling support.

## Steps

1. Copy the closest profile in `factlang/sql/profiles/` (the Postgres profile for most ANSI-like engines).
2. Fill in the decisions: quoting, path slots and `max_table_parts`, conditional counting style, type cast names, `now` expression, case folding of unquoted names, reserved words, type families, parameter types, information-schema template.
3. If it supports sampling, declare `sample_styles` and a `keyhash_expr` following the fixed bucket rule in [[Measurement-API]]; otherwise leave them unsupported so FL506 is raised.
4. Register it:

```python
import dataclasses
import factlang as fl

mine = dataclasses.replace(fl.sql.get_profile("postgres"), name="myengine")
fl.sql.register_profile(mine)
```

5. For a node that prints differently, add an entry to the profile's `overrides` mapping (AST node type to print function) instead of forking the printer.

## Conformance checklist

The shared suite runs every profile and checks: every primitive times every metric renders; metric aliases match the wire format; no `LIMIT`/`TOP`/sample text except where allowed; the NULL-before-group rule is present; the SQL parses in sqlglot for that dialect; hostile literals appear only in parameters. Add execution tests if you can run the engine in CI.

## Tier

A profile run against a real engine in CI is `verified`; one only parse-tested is `native`.

## See also

- [[Dialects]]
- [[sqlglot-Bridge]]
- [[Extending-Connectors]]
- [[Contributing]]
