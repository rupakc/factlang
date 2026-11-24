# Diagnostics

> Every problem FactLang reports has a stable code `FLnnn`, a severity, a message, a span (line and columns) and often a hint or a did-you-mean suggestion.
> The first digit of the code names the stage: 1 lexer, 2 parser, 3 analyzer, 4 planning, 5 rendering, 6 contract completeness, 7 execution.
> Codes never change meaning, so you can match on them in scripts and agents.

## Reading a diagnostic

`fl.lint(text).render()` prints a caret under the offending text. The same data is available as JSON: `code`, `severity` (error, warning, info), `message`, `span`, `hint`, `suggestion`.

## FL1xx: lexer

| Code | Meaning |
|---|---|
| FL101 | unterminated string or quoted identifier |
| FL102 | unexpected character |

## FL2xx: parser

| Code | Meaning |
|---|---|
| FL201 | expected a value |
| FL202 | unclosed list |
| FL203 | unexpected token |
| FL204 | duplicate attribute |
| FL205 | dangling continuation |
| FL206 | malformed filter |
| FL207 | nesting too deep |

## FL3xx: analyzer

| Code | Meaning |
|---|---|
| FL300 | unknown verb (with suggestion) |
| FL301 | wrong operand count |
| FL302 | wrong operand kind or reference depth |
| FL303 | unknown attribute (with suggestion) |
| FL304 | missing required attribute (for example `WITH` without `TO`) |
| FL305 | wrong value type |
| FL306 | operator not allowed |
| FL307 | unusable column list |
| FL308 | filter not allowed for this verb |
| FL309 | invalid value (for example a ratio above 1, bad date, key lists of different length) |
| FL311 | no such table or column |
| FL312 | join across different types without `CAST` (warning) |
| FL313 | could not verify names (info) |

## FL4xx: planning

| Code | Meaning |
|---|---|
| FL404 | a sampled plan appeared in a claim (a contract bug; Amendment B) |
| FL405 | this sample method needs a single read; use `keyhash` |

## FL5xx: rendering (raised as `RenderError`)

| Code | Meaning |
|---|---|
| FL500 | primitive not renderable |
| FL502 | identifier not representable |
| FL503 | cast not expressible |
| FL504 | unknown dialect |
| FL505 | too many table-name parts for the dialect |
| FL506 | sample method not supported by the dialect |

## FL6xx: contract completeness (test time)

| Code | Meaning |
|---|---|
| FL600 | decide reads a fact derive never produces |
| FL601 | decide reads an undeclared attribute |
| FL602 | fewer than 3 valid and 3 invalid examples |
| FL603 | an example does not behave as declared |
| FL604 | incomplete contract |
| FL605 | a filterable verb's plan drops the claim's `WHERE` filter |

## FL7xx: execution (become `error` outcomes)

| Code | Meaning |
|---|---|
| FL700 | execution failed |
| FL701 | over cost budget |
| FL702 | timeout |
| FL703 | metadata SQL refused by the guard |

## See also

- [[Language-Guide]]
- [[Analyzer]]
- [[Connectors]]
- [[Measurement-API]]
