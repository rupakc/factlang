# FactLang

FactLang is a formal language for verifying factual claims against data
warehouses. You write a claim such as "the `orders` table has at least 1,000 rows"
or "every `order_items.order_id` exists in `orders.id`"; FactLang compiles it to
read-only SQL for your dialect, runs it over all the data (never a sample), and
returns a verdict with the evidence behind it. Built for agent answer
verification, schema-registry validation, data-contract CI and migration checks.

**Status: under construction.** Nothing here is usable yet; see
`docs/specs/` for the design.

License: MIT.
