# FactLang grammar (normative)

This document is normative for the syntax of FactLang 1.0. Every fenced block tagged
`factlang-valid` is parsed by `tests/conformance/test_grammar_doc.py` and must parse
without errors; every block tagged `factlang-invalid FLnnn` must report that code.
Blocks whose code is `FL3xx` are checked against the analyser instead (they are
syntactically valid). The grammar knows no verbs: the examples use the standard pack
only to read naturally.

## 1. Properties

1. Line-oriented. A claim ends at a newline. A trailing `\` continues it on the next line.
2. A verb is the first token of a claim and must be an all-uppercase identifier.
3. Attribute names are all-uppercase identifiers. **Operands end at the first
   all-uppercase bare identifier**, so an uppercase bare word is never an operand.
   Quote an uppercase operand: `"ORDERS".id`.
4. Keywords are case-sensitive.

An *uppercase identifier* is an `IDENT` containing at least one letter and no
lowercase letter (`MAX_PER_KEY` is; `_` is not).

## 2. Lexical grammar

```ebnf
IDENT       = ( letter | "_" ) { letter | digit | "_" | "$" } ;
QIDENT      = '"' { any char except '"' or newline | '""' } '"'
            | "`" { any char except "`" or newline | "``" } "`" ;
NUMBER      = [ "-" ] digit { digit | "_" } [ "." digit { digit } ] [ ("e"|"E") ["-"|"+"] digit {digit} ] ;
PERCENT     = NUMBER "%" ;
DURATION    = digit { digit } ( "s" | "m" | "h" | "d" | "w" ) ;
CARDINALITY = "1:1" | "1:N" | "N:1" | "N:M" ;
STRING      = "'" { any char except "'" or newline | "''" } "'" ;
ARROW       = "->" ;
OP          = ">=" | "<=" | "==" | "!=" | "=" | "<" | ">" ;
PUNCT       = "." | "," | "[" | "]" | "(" | ")" ;
COMMENT     = "#" { any char except newline } ;
CONT        = "\" { " " | TAB } NEWLINE ;
```

Strings are single-quoted only; double quotes and backticks delimit identifiers.
`=` is a synonym of `==`. Ordering: `CARDINALITY` before `NUMBER`; `DURATION` before
`NUMBER`; two-character `OP` before one-character; `->` before `-` as a number sign.
An unexpected character is `FL102`; an unterminated string or quoted identifier is
`FL101`; a dangling continuation is `FL205`.

**Continuation.** A `\` followed only by spaces or tabs before the newline joins the next
line to the claim (`CONT` above); the blanks between the `\` and the newline are ignored.
A `\` followed by anything else is `FL102`, and a `\` with no next line is `FL205`.

## 3. Syntactic grammar

```ebnf
document   = { line } EOF ;
line       = [ directive | claim ] [ COMMENT ] NEWLINE ;
directive  = "USE" ref ;
claim      = VERB { operand } { attribute } [ filter ] ;
operand    = edge | ref | literal ;
edge       = ref ARROW ref ;
ref        = name { "." name } ;
name       = IDENT | QIDENT ;
filter     = "WHERE" condition ;
attribute  = ATTRNAME [ OP ] value ;
value      = NUMBER | PERCENT | DURATION | STRING | IDENT | QIDENT
           | CARDINALITY | typed | list ;
typed      = ( "DATE" | "TIMESTAMP" ) STRING ;
list       = "[" [ value { "," value } [ "," ] ] "]" ;
```

Rules enforced by the parser:

- `VERB` and `ATTRNAME` are uppercase identifiers. A first token that is not one is
  `FL203` ("expected a verb").
- A `literal` operand is a `NUMBER`, `PERCENT`, `DURATION` or `STRING`. An
  `IDENT`/`QIDENT` operand is always a ref.
- Every part of a ref is an `IDENT` or `QIDENT`. Anything else after a `.`
  (for example a `CARDINALITY` or `NUMBER`) is `FL203`. A part after a dot may be
  uppercase; only the first token of an operand decides where operands end.
- An attribute value never starts at `WHERE`; a missing value is `FL201`. A repeated
  attribute name is `FL204`. An unclosed list is `FL202`.
- `WHERE` is the only reserved word inside a claim. **A filter is always the last
  element of a claim and runs to the end of the line** (or continuation). Anything
  the filter grammar cannot consume, including a later attribute, is `FL206`.
- `USE` is reserved only as the first token of a line, and must be alone on its line
  apart from a comment. Anything after the ref is `FL203`. Elsewhere `USE` is an
  ordinary attribute name.
- Comments are kept on the claim or directive they trail. Comment-only and blank
  lines produce no item.
- Parentheses, `NOT` chains and lists share one nesting budget of 64 levels; deeper is
  `FL207`.
- A line with any error produces diagnostics and no item; parsing resumes on the next
  line.

## 4. Filters

```ebnf
condition  = conj { "OR" conj } ;
conj       = atom { "AND" atom } ;
atom       = "NOT" atom | "(" condition ")" | comparison | name ;   (* bare boolean column *)
comparison = name OP literal
           | name "IN" list
           | name "NOT" "IN" list
           | name "IS" [ "NOT" ] "NULL"
           | name "BETWEEN" literal "AND" literal ;
literal    = NUMBER | STRING | typed | "TRUE" | "FALSE" ;
```

- Precedence, loosest to tightest: `OR`, `AND`, `NOT`.
- `AND OR NOT IN IS NULL BETWEEN TRUE FALSE DATE TIMESTAMP` are filter keywords,
  recognised **uppercase only**. A bare keyword is never a column name; quote it.
- The column of a comparison is a single name (`IDENT` or `QIDENT`), not a dotted ref.
- A `name` standing alone as an atom tests a boolean column: `deleted` means
  `deleted = TRUE`. A bare `TRUE` or `FALSE` atom is `FL206` (they are keywords; quote a
  column named `true` as `"TRUE"`).
- `x = NULL` is `FL206`: `IS NULL` is the only way to test NULL.
- Inside `IN`, items are filter literals; a trailing comma is allowed.

## 5. Valid examples

A bare boolean column as a filter.

```factlang-valid
ROWS orders WHERE deleted
```

The live-row filter for a flag that is TRUE or NULL.

```factlang-valid
ROWS orders WHERE deleted IS NULL OR NOT deleted
```

A parenthesised bare atom under `NOT`.

```factlang-valid
ROWS orders WHERE NOT (paid) AND amount > 0
```

A table exists.

```factlang-valid
EXISTS orders
```

A column exists.

```factlang-valid
EXISTS orders.customer_id
```

Symbolic arguments are attribute values.

```factlang-valid
TYPE orders.total IS decimal
```

An attribute with an operator.

```factlang-valid
ROWS orders VALUE >=1000
```

`=` is a synonym of `==`; `_` separators are digit grouping.

```factlang-valid
ROWS orders VALUE = 1_717
```

Percentages are ratios.

```factlang-valid
NULLS customers.email RATE <=5%
```

No attributes.

```factlang-valid
DEAD products.legacy_code
```

```factlang-valid
UNIQUE orders.id
```

A composite key is an attribute holding a list.

```factlang-valid
UNIQUE order_items.order_id WITH [line_no]
```

Attribute names may contain underscores.

```factlang-valid
FANOUT order_items.order_id MAX_PER_KEY <=50
```

Strings are single-quoted.

```factlang-valid
VALUES orders.status IN ['new', 'paid', 'shipped']
```

A trailing comma is allowed.

```factlang-valid
VALUES orders.status IN ['new', 'paid',]
```

`''` escapes a quote.

```factlang-valid
VALUES orders.note IN ['it''s fine'] ALLOW <=1%
```

Attributes may follow each other freely.

```factlang-valid
VALUES events.kind IN ['click', 'view'] SPLIT ':'
```

`BETWEEN` is an ordinary attribute name here.

```factlang-valid
RANGE orders.total BETWEEN [0, 100000]
```

Typed literals as attribute values.

```factlang-valid
RANGE orders.created_at BETWEEN [DATE '2020-01-01', DATE '2030-01-01']
```

```factlang-valid
RANGE events.at BETWEEN [TIMESTAMP '2020-01-01T00:00:00Z', TIMESTAMP '2030-01-01T00:00:00Z']
```

Signed, decimal and exponent numbers.

```factlang-valid
RANGE orders.discount BETWEEN [-1.5, 1e2]
```

```factlang-valid
PATTERN customers.email LIKE '%@%.%' RATE >=99%
```

Durations: `s m h d w`.

```factlang-valid
FRESH events.at WITHIN 24h
```

An edge operand.

```factlang-valid
JOIN orders.customer_id -> customers.id MATCH >=99%
```

Cardinality is one token.

```factlang-valid
JOIN orders.customer_id -> customers.id MATCH >=99% CARD N:1 CAST string
```

```factlang-valid
COUNT orders VALUE ==1717 TOLERANCE 0.5%
```

```factlang-valid
DISTINCT orders.customer_id VALUE ==42
```

```factlang-valid
SUM order_items.price VALUE ==1234.5 TOLERANCE 1%
```

```factlang-valid
AVG order_items.price VALUE >=10
```

```factlang-valid
MIN orders.total VALUE >=0
```

```factlang-valid
MAX orders.total VALUE <=100000
```

Uppercase words after the operands are attributes.

```factlang-valid
NOTE customers.age IS years BECAUSE 'self reported'
```

Quote an uppercase identifier that must be an operand.

```factlang-valid
EXISTS "ORDERS".id
```

Backtick and double-quote identifiers; up to four parts.

```factlang-valid
EXISTS `my shop`."Order Items".id
```

Catalog, schema, table, column.

```factlang-valid
EXISTS shop.public.orders.id
```

A comment runs to the end of the line.

```factlang-valid
ROWS orders VALUE >=1 # a comment
```

Comment-only and blank lines produce no item.

```factlang-valid
# a full-line comment
```

A filter is the last element of a claim.

```factlang-valid
ROWS orders WHERE status = 'paid'
```

```factlang-valid
ROWS orders VALUE >=1 WHERE status = 'paid'
```

```factlang-valid
ROWS orders VALUE >=1 WHERE status != 'void' AND total > 0
```

`AND` binds tighter than `OR`: `a = 1 OR (b = 2 AND c = 3)`.

```factlang-valid
ROWS orders VALUE >=1 WHERE a = 1 OR b = 2 AND c = 3
```

`NOT` binds tightest; parentheses group.

```factlang-valid
ROWS orders VALUE >=1 WHERE NOT (a = 1 OR b = 2)
```

```factlang-valid
ROWS orders VALUE >=1 WHERE status IN ['new', 'paid']
```

```factlang-valid
ROWS orders VALUE >=1 WHERE status NOT IN ['void']
```

The only way to test NULL.

```factlang-valid
ROWS orders VALUE >=1 WHERE shipped_at IS NULL
```

```factlang-valid
ROWS orders VALUE >=1 WHERE shipped_at IS NOT NULL
```

The `AND` after `BETWEEN` belongs to it.

```factlang-valid
ROWS orders VALUE >=1 WHERE total BETWEEN 10 AND 20 AND status = 'paid'
```

Booleans are uppercase `TRUE` / `FALSE`.

```factlang-valid
ROWS orders VALUE >=1 WHERE paid = TRUE
```

```factlang-valid
ROWS orders VALUE >=1 WHERE created_at >= DATE '2025-01-31'
```

```factlang-valid
ROWS orders VALUE >=1 WHERE created_at < TIMESTAMP '2025-01-31T12:00:00Z'
```

A column named like a filter keyword is quoted.

```factlang-valid
ROWS orders VALUE >=1 WHERE "IN" = 1
```

A directive sets the default namespace for later lines.

```factlang-valid
USE shop.public
```

`USE` may carry a comment.

```factlang-valid
USE public # reporting schema
```

`USE` mid-line is an ordinary attribute name.

```factlang-valid
NOTE orders.total IS currency USE shop
```

## 6. Invalid examples

Unterminated string.

```factlang-invalid FL101
ROWS orders VALUE >=1 WHERE status = 'paid
```

Unterminated quoted identifier.

```factlang-invalid FL101
EXISTS "orders
```

Unexpected character.

```factlang-invalid FL102
ROWS orders VALUE >=1 @
```

A `:` outside a cardinality is reserved.

```factlang-invalid FL102
EXISTS orders.a:b
```

An attribute needs a value.

A bare atom must stand alone; a second name is not an operator.

```factlang-invalid FL206
ROWS orders WHERE deleted deleted
```

A bare `TRUE` is a keyword, not a column.

```factlang-invalid FL206
ROWS orders WHERE TRUE
```

```factlang-invalid FL201
NULLS customers.email RATE
```

An operator needs a value.

```factlang-invalid FL201
NULLS customers.email RATE <=
```

`WHERE` cannot be a value.

```factlang-invalid FL201
NULLS customers.email RATE WHERE x = 1
```

```factlang-invalid FL201
VALUES orders.status IN [,]
```

A list must be closed.

```factlang-invalid FL202
VALUES orders.status IN ['new', 'paid'
```

Also inside a filter.

```factlang-invalid FL202
ROWS orders WHERE status IN ['new'
```

A claim starts with an all-uppercase verb.

```factlang-invalid FL203
rows orders VALUE >=1
```

```factlang-invalid FL203
'rows' orders
```

A ref part is an identifier or a quoted identifier.

```factlang-invalid FL203
EXISTS orders.1:1
```

```factlang-invalid FL203
EXISTS orders.
```

An arrow needs a right-hand ref.

```factlang-invalid FL203
JOIN orders.customer_id ->
```

Operands come before attributes.

```factlang-invalid FL203
ROWS orders VALUE >=1 customers
```

List items are comma-separated.

```factlang-invalid FL203
VALUES orders.status IN [1 2]
```

`USE` must be alone on its line.

```factlang-invalid FL203
USE shop.public MATCH >=1
```

`USE` needs a namespace.

```factlang-invalid FL203
USE
```

An attribute may appear once.

```factlang-invalid FL204
ROWS orders VALUE >=1 VALUE <=5
```

A claim continues on the next line after a `\`, even with blanks after it.

```factlang-valid
ROWS orders \
  VALUE >=1 \ 	
  WHERE status = 'paid'
```

A continuation needs a following line.

```factlang-invalid FL205
ROWS orders VALUE >=1 \
```

Use `IS NULL`.

```factlang-invalid FL206
ROWS orders WHERE status = NULL
```

A filter needs a condition.

```factlang-invalid FL206
ROWS orders WHERE
```

```factlang-invalid FL206
ROWS orders WHERE status =
```

A filter runs to the end of the line; nothing follows it.

```factlang-invalid FL206
ROWS orders WHERE status = 'a' VALUE >=1
```

An uppercase word after a filter is still part of the filter.

```factlang-invalid FL206
ROWS orders WHERE status = 'a' RATE
```

A filter column is a single name.

```factlang-invalid FL206
ROWS orders WHERE orders.status = 'a'
```

Filter keywords are uppercase only.

```factlang-invalid FL206
ROWS orders WHERE status is null
```

Unclosed parenthesis.

```factlang-invalid FL206
ROWS orders WHERE (a = 1
```

A keyword is not a column name; quote it.

```factlang-invalid FL206
ROWS orders WHERE AND = 1
```

`BETWEEN` needs `AND`.

```factlang-invalid FL206
ROWS orders WHERE total BETWEEN 1 5
```

`IN` takes a bracketed list.

```factlang-invalid FL206
ROWS orders WHERE status IN ('a', 'b')
```

Filter literals are numbers, strings, typed dates and booleans.

```factlang-invalid FL206
ROWS orders WHERE rate = 5%
```

Unknown verb: reported by the analyser, not the parser.

```factlang-invalid FL300
FROBNICATE orders
```

Nesting is bounded.

```factlang-invalid FL207
ROWS orders WHERE (((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((((a = 1)))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))))
```
