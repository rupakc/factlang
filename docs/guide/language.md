# The language

A FactLang document is a list of lines. Each line is a **claim** (one fact about the
warehouse), a **`USE` directive** (a default namespace), a comment or blank. This page
explains the shape of a claim; the [grammar reference](../reference/grammar.md) is the
normative definition, and every example in it is executed by the test suite.

## Anatomy of a claim

```factlang
JOIN orders.customer_id -> customers.id CARD N:1 MATCH >=99% WHERE status = 'paid'
```

| part | here | rule |
|---|---|---|
| verb | `JOIN` | the first token; an all-uppercase word, checked against the active pack |
| operands | `orders.customer_id -> customers.id` | refs, edges (`a -> b`) or literals; they end at the first uppercase word |
| attributes | `CARD N:1`, `MATCH >=99%` | an uppercase name, an optional operator, a value |
| filter | `WHERE status = 'paid'` | optional, **always last**, runs to the end of the line |

The grammar knows no verbs. It parses `VERB operands ATTRIBUTES [WHERE …]` for any verb, and
the analyser then checks the line against the verb's signature. That is why adding a verb is
data, never a change to the parser (see [New verbs](../extending/verbs.md)).

## One claim per line

A claim ends at the newline. A file with five mistakes reports five diagnostics: after an
error the parser skips to the next line and carries on. A trailing backslash continues a
claim, which long value lists need:

```factlang
VALUES orders.status IN ['new', 'paid', 'packed', 'shipped', \
                         'delivered', 'returned', 'refunded']
```

`#` starts a comment that runs to the end of the line. Comments are kept by the formatter
and ignored by everything else.

## References

A reference names a table or a column with one to four dot-separated parts:

| written | means |
|---|---|
| `orders` | a table, in the connection's default schema |
| `shop.orders` | schema `shop`, table `orders` |
| `analytics.shop.orders` | catalog (database, project) `analytics`, schema `shop` |
| `orders.id` | column `id` of table `orders` |
| `analytics.shop.orders.id` | the same column, fully qualified |

Whether a ref is a table or a column is decided by the verb's signature: `ROWS` takes a
table, `UNIQUE` a column. In a column ref the last part is always the column, and the table
parts fill the slots catalog, schema, table from the right. What the slots mean on your
warehouse (a BigQuery project, a Snowflake database, a MySQL database) and how many it
accepts is a property of the dialect; see [Namespaces](namespaces.md).

### Identifiers mean what they mean in your SQL

An unquoted identifier is written to the SQL unquoted, so the warehouse applies its own case
rules: `orders` finds `ORDERS` on Snowflake exactly as your own SQL would. A quoted
identifier keeps its case and may contain spaces:

```factlang
ROWS "Order Items" VALUE >=1
UNIQUE `legacy-orders`.id
```

Double quotes and backticks both delimit identifiers; FactLang writes them with your
dialect's quote character. Strings use **single** quotes only, with `''` for a quote
inside a string, as in SQL.

!!! tip "Uppercase operands"
    Operands end at the first all-uppercase bare word, so an uppercase table name must be
    quoted: `ROWS "ORDERS" VALUE >=1`. Without the quotes, `ORDERS` would be read as an
    attribute.

### Writing names from code

A program that generates claims from warehouse metadata does not have to re-derive these
rules: `factlang.syntax.needs_quoting(name)` says whether a name written bare would denote
something else (an uppercase word, a filter keyword, a space or a leading digit), and
`factlang.syntax.quote_name(name)` returns its double-quoted form, refusing (`ValueError`) an
empty name or one holding a carriage return, a newline or NUL.

```python
import factlang as fl
from factlang.syntax import needs_quoting, quote_name

def name(n: str) -> str:
    return quote_name(n) if needs_quoting(n) else n

print(name("status"), name("ID"), name("order items"), name('say "hi"'))
# status "ID" "order items" "say ""hi"""
assert fl.lint(f"UNIQUE {name('Order Items')}.{name('ID')}").has_errors is False
```

## Values

| kind | examples | notes |
|---|---|---|
| number | `1717`, `1_717`, `-3.5`, `1e6` | `_` groups digits |
| percent | `99%`, `0.5%` | normalised to a ratio at parse time: `99%` is `0.99` |
| duration | `30s`, `15m`, `24h`, `7d`, `2w` | normalised to seconds |
| string | `'paid'`, `'O''Brien'` | single quotes |
| cardinality | `1:1`, `1:N`, `N:1`, `N:M` | one token |
| typed | `DATE '2025-01-31'`, `TIMESTAMP '2025-01-31T12:00:00Z'` | ISO 8601; no offset means UTC |
| list | `['a', 'b']`, `[line_no, shop_id]` | a trailing comma is allowed |
| symbol | `integer`, `string`, `bytes` | a bare lowercase word, e.g. a type family |

An attribute's operator is one of `== != < <= > >=`; `=` is accepted as a synonym of `==`.
Each verb declares which operators an attribute accepts: `RATE` takes `<=` but not `!=`, and
`IS` takes none.

## Canonical form and identity

`fl.format` prints a document in canonical form: single spaces, `==` for `=`, normalised
numbers, comments kept.

```python
import factlang as fl
print(fl.format("ROWS orders VALUE=1_717   # yesterday's count"))
# ROWS orders VALUE == 1717  # yesterday's count
```

Every claim also has a **semantic hash**: SHA-256 over the verb, the resolved operands, the
filter (commutative parts in canonical order) and the attributes sorted by name. It ignores
comments, whitespace, attribute order, numeric spelling and `=` versus `==`, and it is
computed after `USE` expansion, so the same fact written two ways hashes the same. Hosts use
it to deduplicate claims and to keep a history of verdicts per fact.

## See also

- [Grammar reference](../reference/grammar.md): the normative EBNF, with executable examples.
- [Standard verbs](verbs.md): what each verb measures and when it passes.
- [Filters](filters.md): the `WHERE` condition language.
