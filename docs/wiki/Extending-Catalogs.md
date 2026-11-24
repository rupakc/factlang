# Extending Catalogs

> A catalog is two methods: does a table exist, and what columns does it have.
> Return `None` when you cannot answer, so FactLang reports "could not verify" instead of a false error.
> Wrap yours in `CachedCatalog` and never enumerate the whole warehouse.

## The contract

```python
class MyCatalog:
    def table_exists(self, ref):   # True / False / None (unknown)
        ...
    def columns(self, ref):        # {"name": "native type"} or None
        ...
```

Use a catalog by passing it to the checker: `fl.Checker(connector, catalog=MyCatalog())`.

## Guidance

- **Be lazy:** answer only for the table asked about. Do not load a full schema listing at start-up.
- **Be honest:** `False` means definitely not there. If a lookup failed or you lack permission, return `None`.
- **Keep native types:** return the engine's own type text (`NUMBER(38,0)`); the dialect's type-family table maps it to `integer`, `string` and so on.
- **Cache:** wrap with `fl.CachedCatalog(inner, ttl_s=300, max_tables=1000)`.

## Examples of where a custom catalog helps

A schema registry that already holds column metadata, a data catalog service, or a static JSON export (for which `StaticCatalog` is already enough).

## Testing

Check three cases: a known table, a missing table, and an unknown (`None`). Confirm that unknown leads to FL313 info and that a definite miss leads to FL311.

## See also

- [[Catalogs]]
- [[Analyzer]]
- [[Extending-Connectors]]
- [[Contributing]]
