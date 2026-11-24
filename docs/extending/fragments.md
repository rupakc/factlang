# Filter and join fragments

Libraries built on FactLang sometimes need a piece of SQL rather than a verdict: a
profiler's `WHERE`, a join path for a generated query. Writing it by hand would mean a second
set of quoting, parameter and dialect rules that can disagree with FactLang's. Instead, parse
a condition with FactLang's [filter language](../guide/filters.md) and render it through the
same internal SQL tree and identifier rules as every probe. Every code block on this page runs
against DuckDB in the test suite.

```python
import duckdb
import factlang as fl
from factlang.semantics.refs import ColumnRef, EdgeRef, Ident, TableRef

orders = TableRef(None, None, Ident("orders"))
items = TableRef(None, None, Ident("order_items"))
customers = TableRef(None, None, Ident("customers"))
```

## `fl.parse_filter`

```python
f, diagnostics = fl.parse_filter("deleted IS NULL OR NOT deleted")
assert f is not None and len(diagnostics) == 0

bad, diagnostics = fl.parse_filter("status = NULL")
print(diagnostics.render())       # error[FL206]: NULL is not a comparable value … help: use `IS NULL`
assert bad is None and diagnostics.codes() == ["FL206"]
```

`fl.parse_filter(text, *, catalog=None, table=None)` parses and analyses a bare condition
(with or without a leading `WHERE`) and returns `(FilterExpr | None, DiagnosticBag)`; the
filter is `None` when there is any error. With a `catalog` and a `table`, columns are checked
as in a claim (`FL311`, `FL305` for a bare atom on a non-boolean column). Literals become
parameters `p1, p2, …` in source order.

## `fl.sql.render_filter`

```python
frag = fl.sql.render_filter(f, table=items, dialect="snowflake", alias="i")
print(frag.sql_template)   # (i.deleted IS NULL OR NOT (i.deleted = {{p1}}))
print(frag.inline())       # (i.deleted IS NULL OR NOT (i.deleted = TRUE))
sql, params = frag.bind("qmark")
```

The result is a `RenderedFragment`: `sql_template` with neutral `{{name}}` placeholders,
`params`, `dialect`, `tier`, plus `bind(paramstyle)` and `inline()` exactly like a probe. It
is a boolean expression, **not a statement**, and FactLang never executes it. `alias`
qualifies the columns; `param_prefix` renames the parameters so several fragments can share a
statement.

## `fl.sql.render_join`

```python
path = [
    EdgeRef(ColumnRef(items, Ident("order_id")), ColumnRef(orders, Ident("id"))),
    EdgeRef(ColumnRef(orders, Ident("customer_id")), ColumnRef(customers, Ident("id"))),
]
snippet = fl.sql.render_join(path, dialect="duckdb", filters={items: f})
print(snippet.inline())
# FROM order_items t1 INNER JOIN orders t2 ON t1.order_id = t2.id
#   INNER JOIN customers t3 ON t2.customer_id = t3.id
#   WHERE (t1.deleted IS NULL OR NOT (t1.deleted = TRUE))
```

`render_join(edges, dialect, *, filters=None, aliases=None)` produces
`FROM a JOIN b ON … [JOIN c ON …] [WHERE …]` for a path of `EdgeRef`s or
`fl.sql.JoinEdge`s (composite keys and `cast_to` allowed). The rule for filters: the
**left-most** table's filter becomes the `WHERE`; every other table's filter is ANDed into the
`ON` of the join that brings it in.

It is bindable, so a host can wrap it:

```python
conn = duckdb.connect()
conn.execute("CREATE TABLE customers AS SELECT range AS id FROM range(1, 4)")
conn.execute("CREATE TABLE orders AS SELECT range AS id, range AS customer_id FROM range(1, 4)")
conn.execute("""CREATE TABLE order_items AS
    SELECT * FROM (VALUES (1, NULL), (2, FALSE), (3, TRUE)) AS t(order_id, deleted)""")

sql, params = snippet.bind("qmark")
count = conn.execute(f"SELECT COUNT(*) {sql}", params).fetchone()[0]
assert count == 2      # the deleted item is filtered out
```

Every native profile renders both functions; another dialect name goes through the sqlglot
bridge. A hostile literal stays a parameter in every dialect.

## Stability

`fl.parse_filter`, `fl.sql.render_filter`, `fl.sql.render_join` and `RenderedFragment` are
public API under SemVer; see the [Python API](../reference/api.md#stable-api).

## See also

- [Filters](../guide/filters.md)
- [Raw measurement](../guide/measure.md)
