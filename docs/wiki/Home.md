# FactLang

> FactLang is a small language for writing one fact about a data warehouse as one line, plus a Python library that checks that fact against the real data.
> You get back a verdict (pass, fail, and a few others) together with the numbers and the exact SQL that produced it.
> This wiki explains how FactLang is designed, how its parts fit together, and how to use and extend it.

## The problem in one minute

AI assistants, data engineers and dashboards all say things about data: "every order has a customer", "there are 1,717 churned opportunities", "this column only holds four values". Written in prose, those statements cannot be checked, so a wrong one survives and everything built on it is quietly wrong.

FactLang turns each statement into a **claim**: one line of text in a tiny language.

```
JOIN orders.customer_id -> customers.id CARD N:1 MATCH >=99%
NULLS orders.shipped_at RATE <=5%
VALUES opportunities.stage IN ['Prospect', 'Won', 'Lost', 'Churned']
COUNT opportunities VALUE ==1717 WHERE stage = 'Churned'
FRESH events.created_at WITHIN 24h
```

The library compiles each claim to a read-only query for your warehouse, runs it over **all** the matching rows, and tells you whether the claim holds, with evidence.

## Thirty seconds of code

```python
import factlang as fl

# no warehouse needed: check the text and see the SQL it would run
print(fl.lint("UNIQUE orders.id").render())
(probe,) = fl.compile("UNIQUE orders.id", dialect="snowflake")   # one probe per claim

# with a warehouse: one call, one verdict
outcome = fl.check("UNIQUE orders.id", connector=fl.DBAPIConnector(conn, "duckdb"))
print(outcome.verdict, outcome.explanation)
```

## Where to go next

| If you want to... | Read |
|---|---|
| Understand the vocabulary (claims, verdicts, evidence, tiers) | [[Concepts]] |
| See how the pieces fit together | [[Architecture]] |
| Learn to write claims | [[Language-Guide]] and [[Standard-Verbs]] |
| Understand a single component | [[Lexer]], [[Parser]], [[Analyzer]], [[IR]], [[Dialects]], [[sqlglot-Bridge]], [[Optimizer]], [[Connectors]], [[Catalogs]], [[Judge]] |
| Measure tables without claims (for tools built on FactLang) | [[Measurement-API]] |
| Extend FactLang | [[Extending-Verbs]], [[Extending-Dialects]], [[Extending-Connectors]], [[Extending-Catalogs]], [[Extending-Packs]] |
| Plug it into an LLM agent | [[Agent-Integration]] |
| Use the command line | [[CLI]] |
| Decode an error code | [[Diagnostics]] |
| Look up a word | [[Glossary]] and [[FAQ]] |
| Understand why it is built this way | [[Design-Decisions]] |
| Contribute | [[Contributing]] |

## The five promises

1. **Never a guess.** A claim FactLang cannot measure exactly is reported as such, never approximated.
2. **No sampled verdicts.** Claims are judged on all rows. (Raw measurement of huge tables may be sampled, but never a verdict: see [[Measurement-API]].)
3. **No raw SQL in a claim.** Every literal you write becomes a typed bind parameter.
4. **Read-only.** FactLang only ever issues `SELECT`-style queries.
5. **Stable wire formats.** Metric column names and diagnostic codes are fixed, so tools can depend on them.

## See also

- [[Concepts]]
- [[Architecture]]
- [[Language-Guide]]
- [[FAQ]]
