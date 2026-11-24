# Namespaces

Warehouses with thousands of schemas need short claims. A `USE` line sets the default
namespace for every following line of the same document, until the next `USE`:

```factlang
USE analytics.shop
ROWS orders VALUE >=1000                               # analytics.shop.orders
JOIN orders.customer_id -> crm.customers.id MATCH >=99%  # right side: analytics.crm.customers
USE staging
ROWS orders VALUE >=1000                               # staging.orders
```

`USE` produces no outcome. It is reserved only as the first token of a line.

## How a ref is resolved

A table ref has up to three slots, **catalog**, **schema** and **table**, filled from the
right. A column ref has one more part, the column, which is always last. Missing leading
slots are filled from the `USE` namespace; this applies to every ref of the claim, including
the right side of a `JOIN` edge. Slots still missing are left empty and the connection's
defaults apply when the probe runs.

The fully resolved ref is part of the claim's semantic hash, so `USE shop` + `ROWS orders`
and `ROWS shop.orders` are the same fact. Connection defaults are unknown when a claim is
analysed, so they are not.

## What the slots mean on each warehouse

| dialect | catalog | schema | table | max table parts |
|---|---|---|---|---|
| `bigquery` | project | dataset | table | 3 |
| `snowflake` | database | schema | table | 3 |
| `redshift`, `postgres` | database | schema | table | 3 |
| `duckdb` | database | schema | table | 3 |
| `databricks` | catalog | schema | table | 3 |
| `tsql` | database | schema | table | 3 |
| `trino` | catalog | schema | table | 3 |
| `mysql`, `clickhouse` | — | database | table | 2 |
| `oracle` | — | schema (owner) | table | 2 |

A resolved table ref with more parts than its dialect accepts is refused when the SQL is
rendered (`FL505`), for example `analytics.shop.orders` on MySQL. A ref of more than four
parts, or a `USE` that would make one, is `FL302`.

## Case

An unquoted name is written unquoted, so the warehouse applies its own case rules,
including configuration-dependent ones such as MySQL's `lower_case_table_names` or a SQL
Server collation. A quoted name keeps its exact case. If an unquoted name is a reserved word
of the dialect, FactLang quotes it after applying the case the warehouse would have applied
(upper on Snowflake and Oracle, lower on Postgres and Redshift), which is what the unquoted
name would have resolved to.

## See also

- [The language: references](language.md#references)
- [Catalogs](../extending/catalogs.md): how names are checked without enumerating the
  warehouse.
