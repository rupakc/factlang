# Parser

> The parser turns tokens into a generic tree (`Document`) of directives and claims, and it knows no verbs at all.
> When a line is broken it records a diagnostic, skips to the next line, and keeps going, so one file reports every broken line.
> A companion formatter prints the tree back in canonical form (both live at layer 4 and may not import each other).

## A verb-agnostic grammar

The parser reads `VERB operands attributes [WHERE filter]` and builds nodes: `Document`, `Directive`, `Claim`, `Ref`, `Edge`, `Literal`, `Attribute`, filter nodes. It never asks "is this verb real?". That check happens later, in the [[Analyzer]], against a pack. So adding a verb never touches the parser.

Two grammar rules do the heavy lifting:

1. **Operands end at the first all-uppercase bare identifier**, which starts the attributes.
2. **A filter is always last.** `WHERE` runs to the end of the line, so attribute names that double as filter keywords (`IS`, `IN`, `BETWEEN`, `NOT`) are never ambiguous.

## Line recovery

Each line is parsed independently. On an error the parser reports a diagnostic with a span and skips to the end of that line (continuation lines included). Typical codes: FL201 expected a value, FL202 unclosed list, FL203 unexpected token, FL204 duplicate attribute, FL205 dangling continuation, FL206 malformed filter, FL207 nesting too deep.

Nesting of parentheses, `NOT` and lists is capped so hostile input cannot exhaust the stack.

## Normalisation at parse time

- `95%` becomes the ratio `0.95`.
- `1_717` becomes `1717`.
- `24h` becomes `86400` seconds.
- `=` in an attribute becomes `==`.

## The formatter

`fl.format(text)` prints the canonical form. Comments are kept. Formatting twice changes nothing, and `parse(format(x))` has the same semantic hashes as `parse(x)` (both property-tested).

## Semantic hash

The hash (SHA-256 over a canonical form of verb, resolved operands, filter in canonical order, and attributes sorted by name) ignores comments, whitespace, attribute order, numeric spelling and `=` versus `==`. It is computed after `USE` expansion, so the same fact written under two different `USE` lines hashes the same. It also treats the bare `deleted` and `deleted = TRUE` as identical. Connection defaults are not part of identity.

## Parsing a filter alone

`fl.parse_filter("deleted IS NULL OR NOT deleted")` parses and checks a bare condition (with or without `WHERE`), returning the filter tree and diagnostics, with no claim around it. Useful for tools that build SQL fragments. See [[Measurement-API]].

## See also

- [[Lexer]]
- [[Analyzer]]
- [[Language-Guide]]
- [[Diagnostics]]
