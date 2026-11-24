# Catalogs

A catalog answers two questions about a table: does it exist, and what are its columns and
types. The analyser uses it to catch misspelt names (`FL311`) and type mismatches (`FL312`,
`FL305`), and the structure verbs `EXISTS` and `TYPE` are answered from it. Every code block
on this page runs in the test suite.

```py
class Catalog(Protocol):
    def table_exists(self, ref: TableRef) -> bool | None: ...      # None = unknown
    def columns(self, ref: TableRef) -> dict[str, str] | None: ...  # name -> type
```

**Unknown is not an error.** A catalog that cannot answer returns `None`. The analyser
reports `FL311` only on a definite "no", and an `FL313` information note when it could not
check; a structure verb whose catalog cannot answer is `no_data`, never a pass or a fail.

## The ones that ship

| catalog | |
|---|---|
| `fl.NullCatalog()` | knows nothing; skips name checks |
| `fl.StaticCatalog({"orders": {"id": "integer", …}})` | authoritative, from a dict; `StaticCatalog.from_json(path)` reads a JSON file of the same shape |
| `fl.WarehouseCatalog(connector)` | asks the warehouse's `INFORMATION_SCHEMA` (or equivalent), one query per table, **only for tables the claims mention**; never enumerates schemas |
| `fl.CachedCatalog(inner, ttl_s=300, max_tables=10_000)` | a thread-safe TTL + LRU cache over any catalog, unknown answers included |

A `Checker` with a connector and no catalog builds `CachedCatalog(WarehouseCatalog(connector))`
itself. `checker.offline_catalog()` returns the catalog to use when nothing may be executed
(the host's own, or `None` when the Checker built a warehouse catalog), which is what you
pass to a sibling `Checker` that only compiles for another dialect.

```python
import factlang as fl

catalog = fl.StaticCatalog({
    "orders": {"id": "integer", "customer_id": "integer", "status": "varchar"},
    "customers": {"id": "integer", "email": "varchar"},
})
print(fl.lint("UNIQUE orders.stauts", catalog=catalog).render())
assert fl.lint("UNIQUE orders.stauts", catalog=catalog).codes() == ["FL311"]
assert fl.lint("JOIN orders.customer_id -> customers.email MATCH >=99%",
               catalog=catalog).codes() == ["FL312"]
```

In a `StaticCatalog`, unquoted parts of a reference match case-insensitively and quoted
parts exactly; the fullest key (`catalog.schema.table`, then `schema.table`, then `table`)
is tried first.

## Writing a catalog

Any object with the two methods works, for example one backed by your schema registry:

```python
class RegistryCatalog:
    def __init__(self, registry):
        self.registry = registry

    def table_exists(self, ref):
        return ref.table.text in self.registry

    def columns(self, ref):
        return self.registry.get(ref.table.text)

catalog = fl.CachedCatalog(RegistryCatalog({"orders": {"id": "integer"}}))
assert fl.lint("ROWS shipments VALUE >=1", catalog=catalog).codes() == ["FL311"]
```

Return `None` whenever you do not know. Returning `False` or omitting a column for a table you
do not actually know about turns your catalog's ignorance into a user-facing error.

## See also

- [Connectors](connectors.md)
- [Namespaces](../guide/namespaces.md)
