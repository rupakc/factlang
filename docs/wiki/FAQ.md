# FAQ

> Short answers to the questions people ask first: does it write to my warehouse, does it sample, what if my warehouse is not supported, and why did I get no_data.
> For longer explanations follow the links.
> If something here is wrong or missing, open an issue.

**Does FactLang write to my warehouse?** No. It only issues read-only `SELECT`-style queries. Use a read-only account anyway.

**Does it sample my data?** Claims are always judged on all matching rows. Only `Checker.measure` can run a sampled measurement, and it returns raw numbers, never a verdict. See [[Measurement-API]].

**Do I need a database to try it?** No. `fl.lint`, `fl.format` and `fl.compile` work with no connection. See [[Architecture]].

**What does `pip install factlang` install?** Only the core, which uses just the standard library. Drivers and optional features come from extras such as `factlang[duckdb]`.

**My warehouse is not in the native list.** Install `factlang[sqlglot]` and pass its dialect name; you get `best_effort` SQL. Or write a profile: [[Extending-Dialects]].

**Why `no_data` and not `pass`?** If a number the claim needs does not exist (a rate over an empty table), saying `pass` would be claiming something unproven. See [[Judge]].

**What is the difference between `error` and `no_data`?** `no_data` means the query ran but the answer is undecidable. `error` means we could not measure (rejected SQL, timeout, cost limit).

**Why is my uppercase table not found?** An uppercase bare word ends the operands. Quote it: `"ORDERS".id`. See [[Language-Guide]].

**Can I put a SQL expression in a claim?** No. There is deliberately no raw-SQL escape hatch. Use a filter, or add a verb: [[Extending-Verbs]].

**How do I check many facts cheaply?** Put them in one file. Claims on the same table and filter share one scan: [[Optimizer]].

**Can a verb accept different wording, like `CNT`?** Yes, with an alias on a pack: [[Extending-Packs]].

**How do I keep history of a fact?** Store the outcome's semantic hash with the verdict. FactLang does not store history itself.

**Does it support nested JSON paths?** Not yet; the grammar leaves room for them.

**Can an LLM use it?** Yes: [[Agent-Integration]].

## See also

- [[Concepts]]
- [[Glossary]]
- [[Design-Decisions]]
- [[Contributing]]
