# Lexer

> The lexer turns the raw text of a claim file into a flat list of tokens such as verb, number, string, arrow and operator.
> It never raises an exception: an odd character becomes an error token plus a diagnostic and scanning goes on.
> It lives in `factlang/syntax/lexer.py` (layer 2) with token definitions in `syntax/tokens.py` (layer 1).

## What it does

Given text and a diagnostic bag, `tokenize(source, bag)` returns tokens, each carrying its kind and its span (line, column, end column). It recognises:

| Token | Example |
|---|---|
| identifier | `orders`, `MAX_PER_KEY` |
| quoted identifier | `"Order Items"`, `` `x` `` |
| number | `42`, `-3.5`, `1_717`, `1e3` |
| percent | `95%` |
| duration | `24h` |
| cardinality | `N:1` |
| string | `'Won'` |
| arrow | `->` |
| operator | `>= <= == != = < >` |
| punctuation | `. , [ ] ( )` |
| comment | `# note` |
| continuation | `\` at the end of a line |

## Order matters

Several shapes could be read two ways, so the lexer tries them in a fixed order, each pinned by a test:

- `N:1` (cardinality) before a number or identifier.
- `24h` (duration) before a number, but only when the digits are followed by one unit letter and then a non-identifier character, so `24hours` stays an identifier.
- Two-character operators before one-character ones (`>=` before `>`).
- `->` before `-` as a number sign.

## Failing gently

- An unexpected character gives **FL102** and an `ERROR` token. Any colon that is not part of a cardinality is FL102 (colon is reserved for future nested paths).
- An unterminated string or quoted identifier gives **FL101** and ends at the newline, so the damage stays on one line.

Because the lexer never raises, a property test feeds it arbitrary text and asserts it always returns.

## Why a hand-written lexer

Claim lines are tiny and the rules above are order-sensitive. A hand-written scanner keeps error recovery simple (stop at the newline) and keeps the core free of third-party parser libraries.

## See also

- [[Parser]]
- [[Language-Guide]]
- [[Diagnostics]]
- [[Architecture]]
