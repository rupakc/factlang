# Getting started

This page takes you from `pip install` to a report of verdicts in about five minutes. It
uses an in-memory DuckDB database so you need no warehouse; every code block on this page
is executed by the test suite, in order, so what you see is what you get.

## Install

```bash
pip install factlang            # the core: no dependencies at all
pip install "factlang[duckdb]"  # plus DuckDB, used on this page
```

Other extras add a driver for your warehouse: `bigquery`, `snowflake`, `redshift`,
`databricks`, `postgres`, `mysql`, `clickhouse`, `mssql`, `oracle`, `trino`, plus
`sqlalchemy`, `sqlglot` (the long tail of dialects) and `mcp` (the MCP server). `all`
installs every one of them.

## A shop to check

Build a small shop: 500 customers and 2,000 orders. A few things are wrong with it on
purpose: twelve orders point at customers that do not exist, and some orders are
`refunded`, a status nobody mentioned.

```python
import duckdb
import factlang as fl

conn = duckdb.connect()
conn.execute("""
    CREATE TABLE customers AS
    SELECT i AS id,
           CASE WHEN i % 25 = 0 THEN NULL ELSE 'customer' || i || '@example.com' END AS email
    FROM range(1, 501) AS t(i)
""")
conn.execute("""
    CREATE TABLE orders AS
    SELECT i AS id,
           CASE WHEN i % 160 = 0 THEN 10000 + i ELSE 1 + (i * 7) % 500 END AS customer_id,
           CASE i % 10 WHEN 0 THEN 'refunded' WHEN 1 THEN 'open' WHEN 2 THEN 'open'
                       ELSE 'paid' END AS status,
           CAST(5 + (i * 37) % 200 AS DECIMAL(10, 2)) AS amount,
           CASE WHEN i % 10 IN (1, 2) THEN NULL
                ELSE current_date - CAST(i % 30 AS INTEGER) END AS shipped_at
    FROM range(1, 2001) AS t(i)
""")

connector = fl.DBAPIConnector(conn, "duckdb")
```

`DBAPIConnector` wraps any PEP 249 connection: psycopg, snowflake-connector, pyodbc,
oracledb, the Databricks and Trino drivers and so on. The second argument names the SQL
dialect FactLang should write.

## One claim, one call

```python
outcome = fl.check("UNIQUE orders.id", connector=connector)

print(outcome.verdict)       # Verdict.PASS
print(outcome.explanation)   # max_rows_per_key 1 <= 1
assert outcome.verdict is fl.Verdict.PASS
```

`fl.check` returns an `Outcome` when the text holds exactly one claim. The explanation
always names the numbers the verdict was decided on.

## Many claims, one report

Claims are line-oriented: one per line, `#` starts a comment.

```python
claims = """
# what an analyst told us about the shop
JOIN orders.customer_id -> customers.id CARD N:1 MATCH >=99%
NULLS customers.email RATE <=5%
VALUES orders.status IN ['paid', 'open', 'shipped']
COUNT orders VALUE ==1400 WHERE status = 'paid'
SUM orders.amount VALUE ==209000 TOLERANCE 1%
FRESH orders.shipped_at WITHIN 2d
"""

checker = fl.Checker(connector)
report = checker.check(claims)

for o in report.outcomes:
    print(f"{o.verdict.value:8} {o.claim_text}\n         {o.explanation}")

assert [o.verdict.value for o in report.outcomes] == [
    "fail", "pass", "fail", "pass", "pass", "pass",
]
assert not report.ok and not report.verified
```

The output reads:

```text
fail     JOIN orders.customer_id -> customers.id CARD N:1 MATCH >=99%
         match_rate 0.9765625 < 0.99
pass     NULLS customers.email RATE <=5%
         null_rate 0.04 <= 0.05
fail     VALUES orders.status IN ['paid', 'open', 'shipped']
         undeclared_rows 200 != 0
pass     COUNT orders VALUE ==1400 WHERE status = 'paid'
         value 1400 == 1400
pass     SUM orders.amount VALUE ==209000 TOLERANCE 1%
         value 209000 satisfies == 209000 (band 2090)
pass     FRESH orders.shipped_at WITHIN 2d
         age_seconds … <= 172800
```

Two of the analyst's statements were wrong. The report says exactly how wrong:

```python
join, _, values, *_ = report.outcomes
print(join.facts["matched"], "of", join.facts["distinct_left"], "customer ids match")
print(values.facts["top_undeclared"])
assert join.facts["distinct_left"] == 512
assert values.facts["top_undeclared"][0]["value"] == "refunded"
```

`facts` holds every number the verb measured, not only the one it was judged on. A `JOIN`
also reports `coverage` (how much of the right side is reached), `row_match_rate` and the
key multiplicities; see [Standard verbs](guide/verbs.md).

## Look at the evidence

Every measured outcome carries the SQL that produced it:

```python
evidence = report.outcomes[3].evidence
print(evidence.sql)      # SELECT COUNT(*) AS row_count FROM orders WHERE status = 'paid'
print(evidence.params)   # ('paid',)
print(evidence.dialect, evidence.tier)   # duckdb verified
assert evidence.params == ("paid",)
```

`evidence.sql` is the display form, with literals inlined so you can paste it into a SQL
console (a value it cannot inline safely is left as a `{{name}}` placeholder; see
[Evidence](guide/evidence.md#inlining-is-display-oriented-and-fails-closed)). What actually ran was a parameterised statement: `'paid'` travelled as a
typed bind parameter (`evidence.params`), never as SQL text. That holds for every literal
in every claim, which is why a claim cannot inject SQL.

## Reports for people and machines

```python
print(report.to_markdown())
data = report.to_dict()             # JSON-ready; report.to_json() gives the text
assert data["verified"] is False and data["ok"] is False
assert data["summary"]["pass"] == 4 and data["summary"]["fail"] == 2
```

## Mistakes are diagnostics, not exceptions

FactLang never raises on bad input. It reports a coded diagnostic with a caret and, where
it can, a suggestion:

```python
bag = fl.lint("UNIQE orders.id\nNULLS customers.email RATE <=120%\n")
print(bag.render())
assert bag.codes() == ["FL300", "FL309"]
```

```text
error[FL300]: unknown verb UNIQE
  --> 1:1
UNIQE orders.id
^^^^^
did you mean: UNIQUE?

error[FL309]: RATE: ratio must be between 0 and 1 (0% to 100%), got 1.2
  --> 2:30
NULLS customers.email RATE <=120%
                             ^^^^
```

Every code is listed in the [diagnostics reference](reference/diagnostics.md). Inside a
report, an invalid claim is an `invalid` outcome and the other claims still run.

## No warehouse? Compile instead

`fl.compile` renders the SQL for any dialect without connecting to anything. This is what
the [playground](playground.md) does in your browser.

```python
for probe in fl.compile("NULLS customers.email RATE <=5%", dialect="snowflake"):
    print(probe.inline())       # display-oriented: literals inlined, or FL507 if unsafe
```

`probe.sql_template` and `probe.params` are the executable form; `probe.bind("qmark")`
(or `numeric`, `named`, `format`, `pyformat`) produces the driver's spelling.

## Next steps

- Learn the whole language in [The language](guide/language.md) and the
  [standard verbs](guide/verbs.md).
- Run claims from the shell or CI with the [command line](cli.md).
- Let an LLM write and check claims: [Agents and LLMs](agents.md).
- Add your own verb in a few lines: [New verbs](extending/verbs.md).
