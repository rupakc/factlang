# Catalogs

> A catalog answers two questions: does this table exist, and what columns (with types) does it have?
> It lets the analyzer catch typos and wrong types before any data query, and it serves the structure verbs EXISTS and TYPE.
> It is lazy and never lists a whole warehouse, so it works with hundreds of thousands of schemas.

## The protocol

```python
class Catalog(Protocol):
    def table_exists(self, ref: TableRef) -> bool | None: ...      # None = unknown
    def columns(self, ref: TableRef) -> dict[str, str] | None: ...  # name -> type
```

`None` means "I cannot say", which is different from "no".

## Built-in catalogs

| Catalog | What it does |
|---|---|
| `NullCatalog` | knows nothing; every question is unknown |
| `StaticCatalog` | built from a dict or JSON file |
| `WarehouseCatalog(connector)` | runs one `INFORMATION_SCHEMA` query per referenced table, only for tables in the claims being checked |
| `CachedCatalog(inner, ttl_s, max_tables)` | an LRU cache around any catalog |

`WarehouseCatalog` lives in `connectors/catalog.py` because it needs a connector. When a `Checker` has a connector and no catalog it builds `CachedCatalog(WarehouseCatalog(connector))` itself.

## Unknown is not an error

The analyzer raises FL311 (no such column) only on a definite "not there". An unknown answer gives the info diagnostic FL313 and the claim goes ahead. For `EXISTS` and `TYPE` an unknown answer gives `no_data` ("catalog could not answer"), never pass or fail.

## Why it never enumerates

Listing every table of a huge warehouse is slow and expensive. Asking about the handful of tables a claim file mentions is cheap and is all the analyzer needs.

## See also

- [[Analyzer]]
- [[Extending-Catalogs]]
- [[Connectors]]
- [[Standard-Verbs]]
