# Extending Connectors

> A connector needs a dialect name and one `execute` method; the rest is optional.
> Wrap any PEP 249 connection with `DBAPIConnector`, or write a small class for a warehouse with its own client.
> Declare your parameter style and map failures to the standard codes.

## The minimum

```python
class MyConnector:
    dialect = "postgres"          # a profile name
    paramstyle = "pyformat"       # how your driver wants parameters

    def execute(self, sql, params, *, timeout_s):
        rows = my_driver.run(sql, params, timeout=timeout_s)
        return QueryResult(rows=[dict(r) for r in rows], bytes_processed=None)
```

`QueryResult` comes from `factlang.connectors.base`. Rows must be plain dicts keyed by column name; the wire-format metric aliases come back exactly as the SQL names them.

## Optional features

- **Async:** add `aexecute` to serve `Checker.acheck` and `ameasure`.
- **Dry run:** a method that prices a query without running it enables the `max_bytes` guard and `Checker.estimate`. Without it, estimates report `unknown`.
- **bytes_processed:** fill it in when your warehouse reports it; it appears in evidence.

## Errors

Raise errors that carry FL700 for a failed query and FL702 for a timeout; the engine turns them into `error` outcomes. Never swallow a failure into empty rows: an empty result would read as `no_data`.

## Quick path: CallableConnector

If your host already runs SQL, give FactLang a function: `fl.CallableConnector(fn, "snowflake")`.

## Testing

Run a seeded tiny table through `fl.check` for one PASS, one FAIL and one NO_DATA claim. Also test a timeout and a rejected query, and that `run_metadata` works through your connector.

## See also

- [[Connectors]]
- [[Extending-Catalogs]]
- [[Dialects]]
- [[Contributing]]
