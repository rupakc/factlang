# FactLang — Design Specification

Status: APPROVED 2026-11-24 · **Amended 2026-11-24 (Amendments A and B — downstream SchemaGuru requirements, owner-directed, §18–§19)** · Process: Elephant-Goldfish (see §15)

This document is the single source of truth for building FactLang v1. A builder with
no access to the conversation that produced it must be able to implement the library
from this document alone. Where a decision was made by the author of this spec rather
than the project owner, it is marked **[call]**.

---

## 1. Intent

AI agents that answer questions over data warehouses make factual claims: "column
`orders.customer_id` joins `customers.id`", "there are 1,717 churned opportunities",
"`status` only takes the values open/closed", "the table was updated in the last 24h".
Today these claims are prose. Prose cannot be checked, so a wrong claim survives and
every answer built on it is quietly wrong.

FactLang is a small formal language plus a Python library. A FactLang sentence states
one fact about a warehouse; the library compiles it to a read-only query for the
target warehouse, runs it (or hands the SQL to the host to run), and returns a
**verdict with evidence**: the measured numbers, the exact SQL, and an explanation.

```
JOIN orders.customer_id -> customers.id CARD N:1 MATCH >=99%
NULLS orders.shipped_at RATE <=5%
VALUES opportunities.stage IN ['Prospect', 'Won', 'Lost', 'Churned']
COUNT opportunities VALUE ==1717 WHERE stage = 'Churned'
FRESH events.created_at WITHIN 24h
```

The library is open source (MIT) and generic. It must contain no reference to any
company, product or dataset from the project it was conceived in.

### 1.1 Lineage

FactLang is a from-scratch reimplementation of the ideas of an internal predecessor
language. None of its code is copied. The concepts carried forward, each of
which exists because its absence caused a measured wrong answer, are:

1. **Line-oriented claims** — one claim per line, so a file with five mistakes reports
   five diagnostics (error recovery skips to the next line).
2. **A verb-agnostic grammar** — the parser knows no verbs; verbs are validated
   against a registry afterwards. Adding a verb is data, never a parser change.
3. **A claim kind is one object** (`Contract`): syntax, plan, derivation, judgement,
   examples. Registered into a named, versioned pack.
4. **A small dialect-free IR of measurement primitives**, each declaring its result
   arity, so the runner shapes rows from the plan rather than assuming `rows[0]`.
5. **No sampling, ever.** No primitive has a limit/sample field; `LIMIT` returns rows
   in storage order, not at random, and sampled probes have reported 22.5% against
   an exact 6.8%.
6. **NULL semantics declared per metric, not per dialect** (e.g. max-rows-per-key
   excludes NULL keys before grouping; composite keys are grouped, never concatenated).
7. **Metric column aliases are a wire format** pinned by a conformance test.
8. **derive/decide split with three-valued logic** — `derive` returns `None` for a
   fact the data cannot support; `decide` is a plain comparison; undecidable
   propagates instead of collapsing to PASS. (A `None` coerced to `0` satisfies
   `<= 1` and would pass while proving nothing.)
9. **A deliberately weak predicate language** (compare, and/or/not) — serialisable
   and explainable; anything harder is derivation.
10. **Judging never raises**; one broken contract must not sink a batch.
11. **Diagnostics with stable codes grouped by stage, spans and carets**, and
    "did you mean" suggestions.
12. **Percentages normalise at parse time**; **semantic identity hash** ignores
    comments, whitespace, attribute order and numeric spelling.
13. **Executable documentation** — the grammar document's examples and every
    contract's examples are run as tests.
14. **Layering enforced by test** and a **portability test** on the core's imports.

### 1.2 What FactLang adds over the predecessor

| Gap in the predecessor | FactLang v1 |
|---|---|
| Two dialects (BigQuery, Postgres) | 11 native dialect profiles (incl. Trino, Amendment A) + any sqlglot dialect (§8) |
| Host must execute SQL | Optional connectors that execute, with timeouts and cost guards (§9) |
| Refs are exactly `table.column` | Qualified refs up to `catalog.schema.table.column`, quoted identifiers, `USE` namespace directive (§4) |
| Filters only on one verb, by a column list | General safe `WHERE` filter on every measurable verb (§4.5) |
| Schema-fact verbs only | Answer-fact verbs: COUNT/SUM/AVG/MIN/MAX/DISTINCT with tolerance, FRESH, RANGE, PATTERN, EXISTS, TYPE (§6) |
| Execution failure filed as `no_data` | Distinct `ERROR` verdict (§7) |
| Literals restricted to closed character sets | Bind parameters for all literal values (§8.4) |
| One query per claim | Probe fusion: claims over the same table/filter share one scan (§9.3) |
| No agent-facing surface | Tool schema for LLM function calling, JSON I/O, optional MCP server, CLI (§10) |
| Unknown-identifier errors need a full catalog | Lazy, cached, pluggable catalogs that never enumerate the warehouse (§9.2) |

---

## 2. Goals, non-goals, anti-goals

### Goals
- G1. **Easy to use**: verifying one claim is one call (`factlang.check(...)`);
  integrating into an existing project needs no config file.
- G2. **Easy to extend**: new verb, override/alias an existing verb, new attribute
  value type, new dialect profile, new connector, new catalog, new IR primitive —
  each is a documented extension point with a worked example and a test template.
- G3. **Robust front end**: lexer/parser/analyser never raise on any input
  (property-tested), recover per line, emit coded diagnostics with spans.
- G4. **Many warehouses**: BigQuery, Snowflake, Redshift, Postgres, DuckDB,
  Databricks/Spark SQL, ClickHouse, SQL Server/Azure Synapse, MySQL/MariaDB, Oracle,
  Trino (Amendment A) natively; anything else sqlglot can generate, as best-effort.
- G5. **Agent-native**: machine-readable verdicts and diagnostics (JSON), an
  LLM tool definition generated from the active pack, a prompt-ready verb reference.
- G6. **Scale**: works on warehouses with hundreds of thousands of schemas — never
  enumerates the catalog, batches and fuses probes, bounds cost.
- G7. **Correctness over coverage**: a claim FactLang cannot measure exactly is
  reported as such, never approximated.

### Non-goals (v1)
- Writing to the warehouse in any form. FactLang is read-only.
- Measurement history / regression detection (an `Outcome` carries the semantic
  hash so a host can store history; storage is the host's job).
- Nested/STRUCT/JSON field paths in refs (planned for v2; the ref grammar reserves
  no syntax that would block it — see §4.3).
- Natural-language → FactLang translation (that is the agent's job; FactLang gives
  it the tool schema and verb reference to do it).
- Sampled **claim verification**. Claims are always judged on all rows. (Sampled
  *measurement* of very large tables exists only through `Checker.measure` with an
  explicit `SampleSpec` — Amendment B, §19.)

### Anti-goals (would make v1 a failure even if it "works")
- A verdict of PASS produced from missing, NULL-coerced or sampled data.
- A raw-SQL escape hatch inside a claim.
- A dialect whose metric aliases differ from the wire format.
- A core that cannot be installed with zero third-party dependencies.
- Any mention of the originating company, its datasets or table names.

---

## 3. Constraints

- License: MIT. Copyright line: `Copyright (c) 2026 Rupak Chakraborty` (owner-confirmed).
- Package name `factlang` (free on PyPI as of 2026-11-24). Import name `factlang`.
- Python **3.10–3.13** supported **[call]**; CI tests all four.
- **Core is stdlib-only.** Optional extras add dependencies (§11). A portability test
  walks the AST of every core module and fails on a non-stdlib import.
- Fully typed; `mypy --strict` is **enforced** in CI (not advisory); `ruff check` and
  `ruff format --check` enforced.
- Build backend: `hatchling` **[call]**; `src/` layout.
- Repository: `github.com/rupakc/factlang`, public, default branch `main`, commits
  pushed incrementally to `main`.

---

## 4. The language

### 4.1 Design properties
1. Line-oriented. A claim ends at a newline. A trailing `\` continues a claim on the
   next line (new in FactLang; long `VALUES` lists need it).
2. The grammar knows no verbs. A verb is the first token of a claim and must be an
   all-uppercase identifier; it is validated against the active pack.
3. Attribute names are all-uppercase identifiers. **Operands end at the first
   all-uppercase bare identifier.** Consequence (keep from the predecessor, documented as
   grammar): an uppercase bare word can never be an operand; symbolic arguments are
   attribute values (`UNIT t.c IS bytes`). Identifiers that are uppercase *and* must
   be operands (e.g. a table literally named `ORDERS` in Snowflake) are written
   quoted: `"ORDERS".id`.
4. Keywords are case-sensitive. Identifier handling is defined once, in §8.4.

### 4.2 Lexical grammar (EBNF)

```ebnf
IDENT       = ( letter | "_" ) { letter | digit | "_" | "$" } ;
QIDENT      = '"' { any char except '"' or newline | '""' } '"'
            | "`" { any char except "`" or newline | "``" } "`" ;
NUMBER      = [ "-" ] digit { digit | "_" } [ "." digit { digit } ] [ ("e"|"E") ["-"|"+"] digit {digit} ] ;
PERCENT     = NUMBER "%" ;
DURATION    = digit { digit } ( "s" | "m" | "h" | "d" | "w" ) ;
CARDINALITY = "1:1" | "1:N" | "N:1" | "N:M" ;
STRING      = "'" { any char except "'" or newline | "''" } "'" ;
ARROW       = "->" ;
OP          = ">=" | "<=" | "==" | "!=" | "=" | "<" | ">" ;
PUNCT       = "." | "," | "[" | "]" | "(" | ")" ;
COMMENT     = "#" { any char except newline } ;
CONT        = "\" NEWLINE ;
```

Changes from the predecessor, and why:
- **Strings are single-quoted only**; double quotes and backticks are *identifiers*
  (SQL convention, so agents trained on SQL write them correctly). `''` escapes a quote.
- `=` is accepted in `WHERE` filters (SQL habit); in attributes `=` is a synonym of
  `==` and the formatter canonicalises it to `==`.
- `DURATION` for `FRESH`. Lexed before `IDENT` only when a digit run is immediately
  followed by one unit letter and then a non-identifier character.

Ordering rules, each pinned by a test: `CARDINALITY` before `NUMBER`; `DURATION`
before `NUMBER`; two-char `OP` before one-char; `->` before `-` as number sign.

The lexer never raises. An unexpected character yields `FL102` and an `ERROR` token;
an unterminated string/quoted identifier yields `FL101` and ends at the newline.

### 4.3 Syntactic grammar (EBNF)

```ebnf
document   = { line } EOF ;
line       = [ directive | claim ] [ COMMENT ] NEWLINE ;
directive  = "USE" ref ;                               (* see 4.4 *)
claim      = VERB { operand } { attribute } [ filter ] ;  (* filter is LAST *)
operand    = edge | ref | literal ;
edge       = ref ARROW ref ;
ref        = name { "." name } ;
name       = IDENT | QIDENT ;
filter     = "WHERE" condition ;                       (* see 4.5 *)
attribute  = ATTRNAME [ OP ] value ;
value      = NUMBER | PERCENT | DURATION | STRING | IDENT | QIDENT
           | CARDINALITY | list ;
list       = "[" [ value { "," value } [ "," ] ] "]" ;
```

`WHERE` is reserved: it is the only uppercase word the parser treats specially
inside a claim, and **a filter is always the last element of a claim and runs to
the end of the line** (or continuation). This is what lets attribute names such as
`IS`, `IN`, `BETWEEN`, `NOT` also be filter keywords without ambiguity:
`RANGE t.c BETWEEN [0, 100] WHERE x = 1` parses one way only. An attribute after a
filter is impossible by construction (it would be read as part of the filter and
fail as `FL206`). `USE` is reserved only as the first token of a line.

`CARDINALITY` is lexed before `IDENT` and only as one of the four exact spellings
followed by a non-identifier character, so `N:1` is one token; any other `:` is
`FL102` (reserved for v2 nested paths).

**Ref resolution.** A ref has 1–4 parts. Its meaning (table ref vs column ref) is
decided by the verb's operand signature, not the parser:
- table ref: `[catalog.][schema.]table`
- column ref: `[catalog.][schema.]table.column` — the last part is always the column.
The table parts are right-aligned into the slots (catalog, schema, table). Missing
leading slots are filled from the `USE` namespace (§4.4), which applies to **every**
ref in the claim, including the right side of a `JOIN` edge; slots still missing
after that are left empty and the connection's defaults apply at execution. A
column ref with 5+ parts is `FL302`. Composite keys use `WITH [col, ...]`, never a
deeper ref. (Nested field paths are a v2 non-goal; v2 may add them with `:`.)

What the slots mean, and how many a dialect accepts (`max_table_parts`), is a
profile fact (§8.3):

| Profile | catalog | schema | table | max parts |
|---|---|---|---|---|
| bigquery | project | dataset | table | 3 |
| snowflake | database | schema | table | 3 |
| redshift, postgres | database | schema | table | 3 |
| duckdb | database | schema | table | 3 |
| databricks | catalog | schema | table | 3 |
| tsql | database | schema | table | 3 |
| trino | catalog | schema | table | 3 |
| mysql, clickhouse | — | database | table | 2 |
| oracle | — | schema (owner) | table | 2 |

A resolved table ref with more parts than `max_table_parts` is refused at render
time as `FL505`.

### 4.4 Directives

`USE catalog.schema` (or `USE schema`) sets the default namespace for subsequent
lines in the same document until the next `USE`. Directives produce no outcome.
This is what makes claims short on warehouses with very many schemas.

### 4.5 Filters

```ebnf
condition  = conj { "OR" conj } ;
conj       = atom { "AND" atom } ;
atom       = "NOT" atom | "(" condition ")" | comparison | name ;   (* bare boolean column, Amendment A *)
comparison = name OP literal
           | name "IN" list
           | name "NOT" "IN" list
           | name "IS" [ "NOT" ] "NULL"
           | name "BETWEEN" literal "AND" literal ;
literal    = NUMBER | STRING | typed | "TRUE" | "FALSE" ;
typed      = ( "DATE" | "TIMESTAMP" ) STRING ;   (* ISO-8601; validated by the analyser *)
```

Inside a filter, `AND OR NOT IN IS NULL BETWEEN TRUE FALSE DATE TIMESTAMP` are
filter keywords; the filter runs to the end of the claim. Column names that collide
with a filter keyword must be quoted. Filters reference columns of the claim's
(left) table only **[call]**. Every literal becomes a **typed** bind parameter
(§8.4): NUMBER → integer or decimal, STRING → string, TRUE/FALSE → boolean,
`DATE '…'` → date, `TIMESTAMP '…'` → timestamp (no offset = UTC). An invalid ISO
string is `FL309`. `x IS NULL` is the only way to test NULL; `x = NULL` is `FL206`.
**Bare boolean atom (Amendment A, R9):** a `name` standing alone as an atom tests a
boolean column for truth: `deleted` ≡ `deleted = TRUE`, so `NOT deleted` is
`NOT (deleted = TRUE)` — which, under SQL three-valued logic, excludes NULL rows. The
idiomatic live-row filter for a flag that is TRUE or NULL is therefore
`deleted IS NULL OR NOT deleted`. The analyser reports `FL305` when the catalog knows the
column is not boolean. The formatter prints the bare form back as written (`deleted`),
and the semantic hash treats `deleted` and `deleted = TRUE` as identical.
Rulings (Amendment A, 2026-11-24): (a) a bare `TRUE` or `FALSE` atom (`WHERE TRUE`,
`WHERE NOT FALSE`) is **`FL206`** — they are filter keywords, not names; a column literally
named `true`/`TRUE` must be quoted (`"TRUE"`). (b) The non-boolean check **reuses `FL305`
("wrong value type")** for the filter column; when the catalog cannot answer (unknown /
`NullCatalog`), the atom is accepted and only the usual `FL313` info is emitted — never an
error on unknown.
A filter is part of the claim's semantic identity. Each verb declares
`filterable: bool`; a filter on a non-filterable verb is `FL308`. Filterable in the
standard pack: `ROWS NULLS UNIQUE FANOUT VALUES RANGE PATTERN FRESH JOIN COUNT
DISTINCT SUM AVG MIN MAX`; not filterable: `EXISTS TYPE DEAD NOTE`.

Typed literals (`DATE '2025-01-31'`, `TIMESTAMP '2025-01-31T12:00:00Z'`) are also
valid attribute values wherever a verb's `AttrSpec` accepts the `temporal` value
type (`RANGE`, `MIN`, `MAX`).

### 4.6 Semantics
- Percentages normalise to ratios at parse time (`95%` ≡ `0.95`).
- `_` digit separators are removed at parse time (`1_717` ≡ `1717`).
- Durations normalise to seconds.
- Semantic identity: `semantic_hash` = SHA-256 over a canonical serialisation of
  (verb, resolved operands, filter AST in canonical order of commutative operands,
  attributes sorted by name with normalised values). Ignores comments, whitespace,
  attribute order, numeric spelling, `=` vs `==`. Resolved means after `USE`
  expansion (slots still empty hash as empty; connection defaults are unknown at
  analysis and are not part of identity), so the same fact under two `USE`
  spellings hashes identically.
- `formatter.format(doc)` prints the canonical form; `parse(format(x))` has the same
  semantic hashes as `parse(x)` (property-tested). Comments are preserved.

---

## 5. Pipeline and architecture

```
text ─lexer→ tokens ─parser→ Document(AST) ─analyzer→ CheckedClaim
     ─planner(contract.plan)→ ProbePlan (IR) ─optimizer→ FusedPlan
     ─dialect→ RenderedProbe(sql, params) ─connector→ rows ─judge→ Outcome
```

Front end (lexer, parser, analyser) is pure and knows no warehouse. Only the
dialect knows SQL; only the connector knows a network.

### 5.1 Package layout and layers

A module may import only from strictly lower layers (enforced by `test_layering.py`).
Within one layer number, modules are ordered by sub-layer and may import only
earlier ones: layer 5 = `refs` (5.0) < `types` (5.1) < `filters` (5.2) <
`signature` (5.3); layer 6 = `catalog` (6.0) < `checked` (6.1); layer 7 =
`identity` (7.0) < `analyzer` (7.1). `parser` and `formatter` (both 4) may not
import each other.

| Layer | Module | Responsibility |
|---|---|---|
| 0 | `factlang/diagnostics.py` | `Span`, `Severity`, `Diagnostic`, `DiagnosticBag`, caret rendering, JSON form |
| 1 | `factlang/syntax/tokens.py` | `TokenKind`, `Token` |
| 2 | `factlang/syntax/lexer.py` | text → tokens, never raises |
| 3 | `factlang/syntax/ast.py` | generic AST: `Document`, `Directive`, `Claim`, `Ref`, `Edge`, `Literal`, `Attribute`, `Filter` nodes |
| 4 | `factlang/syntax/parser.py` | tokens → AST, line recovery |
| 4 | `factlang/syntax/formatter.py` | AST → canonical text |
| 5 | `factlang/semantics/types.py` | `ValueType` registry (built-in + custom) |
| 5 | `factlang/semantics/signature.py` | `OperandSpec`, `AttrSpec`, `VerbSpec`, `VerbRegistry` (suggestions) |
| 6 | `factlang/semantics/catalog.py` | `Catalog` protocol, `NullCatalog`, `StaticCatalog`, `CachedCatalog` |
| 6 | `factlang/semantics/checked.py` | `CheckedClaim` (resolved, typed, immutable) |
| 7 | `factlang/semantics/analyzer.py` | AST + registry + catalog → `CheckedClaim`s + diagnostics |
| 7 | `factlang/semantics/identity.py` | `semantic_hash` |
| 8 | `factlang/ir/` | primitives, metrics, filter IR, `Arity` |
| 9 | `factlang/judge/` | `Predicate` language, `Outcome`, `Verdict`, `judge()` |
| 10 | `factlang/contract.py` | `Contract`, `Example`, `ContractPack`, decorator API |
| 11 | `factlang/packs/standard/` | the standard verb pack, one file per verb |
| 12 | `factlang/sql/` | internal SQL AST, `DialectProfile`, native profiles, renderer |
| 12 | `factlang/sql/sqlglot_bridge.py` | optional; imported lazily |
| 13 | `factlang/optimizer.py` | probe fusion and de-duplication |
| 14 | `factlang/connectors/` | `Connector` protocol + optional adapters (lazy imports); `connectors/catalog.py` holds `WarehouseCatalog` (it needs a connector) |
| 15 | `factlang/engine.py` | `Checker` orchestration, sync + async, batching, limits |
| 16 | `factlang/api.py`, `factlang/__init__.py` | public facade |
| 17 | `factlang/agent/` | tool schema, verb reference, JSON I/O, optional MCP server |
| 17 | `factlang/cli.py` | `factlang` command |

Core = layers 0–13 + `connectors/base.py` + `engine.py` + `api.py` + `agent/` minus
the MCP server. Core is stdlib-only.

---

## 6. Standard verb pack (`factlang.packs.standard`, name `standard`, version `1.0`)

Each verb is one file exporting one `Contract`. Each has ≥3 valid and ≥3 invalid
`Example`s (invalid ones name their expected code). Filterable = accepts `WHERE`.

### 6.1 Structure facts (catalog-only, no data scan)
| Verb | Form | Facts | Passes when |
|---|---|---|---|
| `EXISTS` | `EXISTS t` / `EXISTS t COLUMN c` (a bare `a.b` operand is always a table ref, so a column is named by the `COLUMN` attribute — avoids the schema.table vs table.column ambiguity) | `exists` | object exists |
| `TYPE` | `TYPE t.c IS integer` | `actual_type`, `family` | normalised type family equals the value (families: `integer, decimal, float, string, boolean, date, timestamp, time, bytes, json, array, struct, other`) |

These contracts set `catalog_facts` (a third contract mode beside `plan` and
`unprobeable_reason`; exactly one of the three) and are evaluated through the `Catalog`, using the `MetadataQuery` primitive
(§8.1) when the catalog is warehouse-backed. When a `Checker` has a connector and no
catalog, it builds `CachedCatalog(WarehouseCatalog(connector))` itself **[call]**.
A catalog answer of "unknown" (`None`, including `NullCatalog`) gives `NO_DATA`
with "catalog could not answer" — never PASS, never FAIL. Native type → family
mapping is a per-profile table (`DialectProfile.type_families`: ordered regex →
family), which first unwraps wrappers (`Nullable(…)`, `LowCardinality(…)`), and
maps scale-0 decimals (`NUMBER(38,0)`, `NUMERIC(p,0)`, `DECIMAL(p,0)`) to `integer`.
Each profile's table is tested against a fixture list of that engine's type names.

### 6.2 Data-quality facts
| Verb | Form | Primitive | Facts | Decide |
|---|---|---|---|---|
| `ROWS` | `ROWS t VALUE >=1000` | ColumnAggregate(ROW_COUNT) | `row_count` | Compare VALUE (ops all) |
| `NULLS` | `NULLS t.c RATE <=5%` | ColumnAggregate(ROW_COUNT, NON_NULL) | `null_rate` (None if 0 rows) | Compare RATE (ops `== <= < >= >`) |
| `DEAD` | `DEAD t.c` | as NULLS | `null_rate` | `null_rate == 1` |
| `UNIQUE` | `UNIQUE t.c [WITH [c2,...]]` | ColumnAggregate / CompositeKeyAggregate (NON_NULL, DISTINCT_KEYS, MAX_PER_KEY, DUPLICATE_KEYS) | `duplicate_keys` (number of distinct complete keys held by >1 row), `max_rows_per_key` | `max_rows_per_key <= 1` |
| `FANOUT` | `FANOUT t.c MAX_PER_KEY <=3 [WITH [...]]` | same | `max_rows_per_key` | Compare MAX_PER_KEY |
| `VALUES` | `VALUES t.c IN ['a', 'b'] [SPLIT ':'] [ALLOW <=1%]` | ValueMembership | `undeclared_rows`, `undeclared_distinct`, `top_undeclared` (≤ `report_limit` {value,count}), `coverage` | `undeclared_rows == 0`; with `ALLOW`: `1 - coverage <= ALLOW` |
| `RANGE` | `RANGE t.c BETWEEN [0, 100]` (numbers or typed temporal literals; a bare string is `FL305`, a string inside the pair `FL309`; DATE bounds over a TIMESTAMP column mean the half-open UTC interval [lo 00:00Z, hi+1d 00:00Z)) | ColumnAggregate(MIN_VALUE, MAX_VALUE) | `min_value`, `max_value`, `in_range` | `in_range` is true |
| `PATTERN` | `PATTERN t.c LIKE '%@%.%' RATE >=99%` | PatternCoverage | `match_rate` = matching / non_null | Compare RATE |
| `FRESH` | `FRESH t.c WITHIN 24h` | ColumnAggregate(MAX_VALUE, NOW) | `age_seconds` | `age_seconds <= WITHIN` |

Exact fact definitions (these are the product; each is pinned by execution tests):
- `null_rate` = (row_count − non_null) / row_count; `None` when row_count = 0.
- `VALUES`: the column is compared **as text** (`CAST(col AS string)`, then `TRIM`
  and prefix-before-separator when `SPLIT` is set), **case-sensitive**, against the
  declared list rendered as text (numbers in canonical form, e.g. `1`, `2.5`).
  NULLs are excluded. `coverage` is **by rows**: declared_rows / non_null_rows.
  Membership is computed in SQL so the result size is bounded on any cardinality.
- `FRESH`: the probe returns `max_value` and `server_now` in one query; Python
  computes `age_seconds`. TIMESTAMP without zone and DATE are interpreted as UTC
  (DATE = midnight). If `max_value` is not a date/timestamp (e.g. a STRING column),
  `derive` returns `None` → `NO_DATA` with "not a temporal value"; when the catalog
  knows the column type is non-temporal, the analyser reports `FL305` first.
  An empty table → `NO_DATA`.

### 6.3 Relationship facts
| Verb | Form | Primitive | Facts | Decide |
|---|---|---|---|---|
| `JOIN` | `JOIN a.x -> b.y MATCH >=99% [CARD N:1] [CAST string] [COVERAGE >=50%] [WITH [x2, ...] TO [y2, ...]]` | KeyOverlap | `match_rate`, `coverage`, `row_match_rate`, `fan_in`, `distinct_left`, `distinct_right`, `matched`, `left_non_null`, `max_left_per_key`, `max_right_per_key` | MATCH compare, COVERAGE compare when present, and CARD consistent with multiplicities (`N:1` ⇒ right ≤1, `1:1` ⇒ both ≤1, `1:N` ⇒ left ≤1) |

The filter on `JOIN` applies to the left side only. `match_rate` = matched distinct
non-NULL left keys / distinct non-NULL left keys (`None` when there are none). NULL
left keys are excluded — their share is a `NULLS` claim, not a join claim. Without
`CAST`, keys are compared in their native types; with `CAST <family>` both sides
are cast to that type family.

**Amendment A (R3, R4) — composite keys and reverse facts.**
- *Composite join keys*: `WITH [x2, ...]` lists further LEFT-table columns and
  `TO [y2, ...]` the matching RIGHT-table columns, positionally:
  `JOIN order_items.order_id -> orders.order_id WITH [shop_id] TO [shop_id] MATCH >=99%`
  joins on `(order_id, shop_id) = (order_id, shop_id)`. `WITH` and `TO` must appear
  together (`FL304` naming the missing one) and have equal length (`FL309`). A key
  tuple with any NULL part is excluded on its side (same rule as composite keys in
  §8.1). `CAST` applies to every key part.
- *Additional facts* (all `None` when undecidable, never 0):
  `coverage` = matched distinct right keys / distinct non-NULL right keys (reverse
  support — the share of the right side reached; `None` when the right side has no
  non-NULL keys); `row_match_rate` = left rows with a non-NULL key that matched /
  left rows with a non-NULL key; `fan_in` = `left_non_null / distinct_left` (left rows
  per distinct left key); `distinct_left`, `distinct_right`, `matched`,
  `left_non_null` are exposed as facts.
- *`COVERAGE`* (RATIO, ops `>= == <= < >`, optional) is decided like `MATCH` against
  the `coverage` fact.

### 6.4 Answer facts (what agents state in answers)
| Verb | Form | Primitive | Facts |
|---|---|---|---|
| `COUNT` | `COUNT t VALUE ==1717 [TOLERANCE 0.5%] [WHERE …]` | ColumnAggregate(ROW_COUNT) | `value` |
| `DISTINCT` | `DISTINCT t.c VALUE ==42 [WHERE …]` | ColumnAggregate(DISTINCT_KEYS) | `value` |
| `SUM` `AVG` `MIN` `MAX` | `SUM t.c VALUE ==1234.5 [TOLERANCE 1%] [WHERE …]` | ColumnAggregate(SUM/AVG/MIN_VALUE/MAX_VALUE) | `value` |

`TOLERANCE` and `ABS_TOLERANCE` are mutually exclusive (`VerbSpec.exclusive`, FL309). `TOLERANCE` is relative when a percent/ratio is given with `%` (`1%`), absolute when
given with attribute `ABS_TOLERANCE 0.01`; both absent = exact (`==` uses
`math.isclose(rel_tol=1e-9, abs_tol=1e-9)`). `SUM`/`AVG` on an empty set derive
`value=None` → `NO_DATA`, never 0. Results are converted from `Decimal` exactly
(no float round-trip before comparison when both sides are Decimal-representable).

### 6.5 Semantic (unprobeable) verbs
`NOTE t.c IS unit BECAUSE '...'` is shipped as the reference example of an
unprobeable contract: it parses, analyses, gets a hash, and is reported as
`UNPROBEABLE` with its reason. Hosts can model their own semantic facts this way.

---

## 7. Verdicts and outcomes

```python
class Verdict(str, Enum):
    PASS = "pass"            # measured, predicate true
    FAIL = "fail"            # measured, predicate false
    NO_DATA = "no_data"      # measured, but a fact is undecidable (e.g. 0 rows)
    ERROR = "error"          # could not measure: execution error, timeout, cost limit, CL5xx
    UNPROBEABLE = "unprobeable"  # semantic by design
    INVALID = "invalid"      # did not pass the front end (diagnostics attached)
```

`Outcome` (frozen dataclass, JSON-serialisable via `to_dict()`): `claim_text`,
`canonical_text`, `semantic_hash`, `verb`, `verdict`, `explanation` (names the
numbers, e.g. `match_rate 0.9774 < 0.99`), `facts`, `evidence` (`sql`, `params`,
`dialect`, `tier` ∈ {`verified`,`native`,`best_effort`}, `duration_ms`,
`bytes_processed` when the connector reports it, `fused_with` hashes), `diagnostics`.

`ERROR` is never `NO_DATA`: a warehouse rejecting the SQL is a defect, not an
absence of data.

---

## 8. Dialects (native profiles + sqlglot bridge)

### 8.1 IR primitives (closed core set, open for registration)

| Primitive | Fields | Arity | Result columns (wire format) |
|---|---|---|---|
| `ColumnAggregate` | `table`, `column?`, `metrics`, `filter?` | SINGLE_ROW | metric aliases |
| `CompositeKeyAggregate` | `table`, `columns`, `metrics`, `filter?` | SINGLE_ROW | metric aliases |
| `ValueMembership` | `table`, `column`, `declared` (tuple of text; **may be empty** — then every value is undeclared and the ranked list is the exact top-k observed values, Amendment A R2), `split?`, `report_limit` (default 20, max 1000), `filter?` | ROW_SET | one summary row (`kind='summary'`, `non_null`, `declared_rows`, `undeclared_distinct`) plus ≤ `report_limit` rows (`kind='undeclared'`, `value`, `freq`) ranked by freq desc, value asc |
| `KeyOverlap` | `left`, `right` (each a non-empty tuple of column refs on one table, equal length — Amendment A R3), `cast_to?`, `measure_multiplicity`, `filter?` (left side), `right_filter?` (right side, IR-only — Amendment A R4) | SINGLE_ROW | `distinct_left`, `matched`, `distinct_right`, `left_non_null`, `matched_left_rows`, (`max_left_per_key`, `max_right_per_key`) |
| `PatternCoverage` | `table`, `column`, `pattern`, `filter?` | SINGLE_ROW | `non_null`, `matching` |
| `MetadataQuery` | `table`, `column?` | ROW_SET | `column_name`, `data_type` |

Metrics and aliases: `ROW_COUNT→row_count`, `NON_NULL→non_null`,
`DISTINCT_KEYS→distinct_keys`, `MAX_PER_KEY→max_rows_per_key`,
`MIN_VALUE→min_value`, `MAX_VALUE→max_value`, `SUM→sum_value`, `AVG→avg_value`,
`DUPLICATE_KEYS→duplicate_keys`, `NOW→server_now`. Never `rows` (reserved in BigQuery).

NULL rules (in the IR, so every dialect inherits them): `MAX_PER_KEY` excludes rows
with any NULL key column before grouping; `DISTINCT_KEYS` over a tuple groups by the
columns (never concatenates); `non_null` over a tuple counts rows with a complete key;
`PatternCoverage` matches case-folded (`LOWER(col) LIKE lower(pattern)`) over non-NULL
values; `ValueMembership` excludes NULL and with `split` compares the trimmed prefix
before the first separator.

No primitive samples its **input** unless its `sample` field is set (Amendment B, §19),
which only `Checker.measure` accepts and no claim, contract or analyser ever produces:
for claims every count is over all rows that match the filter. The single permitted row limit is `ValueMembership.report_limit`,
which truncates the *ranked list of undeclared values reported*, after exact
aggregation; the summary counts are exact regardless. The conformance suite asserts
that `LIMIT`/`TOP`/`FETCH FIRST` appears only in that primitive, only on the
outermost ranked list, and never `SAMPLE`/`TABLESAMPLE`/sample predicates anywhere **except** in a plan whose
`sample` field is set (Amendment B). A custom primitive registered
by a plugin must declare its arity and result columns and supply a builder to the
internal SQL AST (§8.2); otherwise rendering it is `FL500`.

### 8.2 Internal SQL AST

Primitives are lowered to a small internal, dialect-free SQL AST
(`factlang/sql/ast.py`): `Select`, `From`, `Join`, `Where`, `GroupBy`, `Subquery`,
`Column`, `Param`, `Agg(fn, arg, distinct)`, `Count`, `CountIf`, `Lower`, `Trim`,
`Like`, `Cast(type_family)`, `Coalesce`, `Case`, `Now`, `Compare`, `And/Or/Not`,
`InList`, `IsNull`, `Between`. Every primitive lowers through this one module
(`factlang/sql/lower.py`), so both rendering paths start from the same tree and
cannot disagree on semantics.

### 8.3 Two rendering paths

1. **Native printer** (`factlang/sql/printer.py`) prints the internal AST using a
   `DialectProfile` — a frozen dataclass of small decisions:
   `name`, `quote_ident(name)`, `qualify(catalog, schema, table)`,
   `count_if` style (`COUNTIF(x)` / `SUM(CASE WHEN x THEN 1 ELSE 0 END)` /
   `COUNT_IF(x)`), `cast_type(family)` map, `string_concat`, `now_expr`,
   `limit_style` (`LIMIT n` / `TOP n` / `FETCH FIRST n ROWS ONLY`),
   `max_table_parts`, `unquoted_case` (`upper`/`lower`/`preserve`),
   `reserved_words`, `type_families`, `param_types` (family → driver type name,
   e.g. BigQuery `INT64`), `supports_filter_clause`, `max_identifier_length`,
   `information_schema` query template, plus an optional `overrides` mapping from
   internal-AST node type to a print function for the rare node a profile must spell
   differently.
   Native profiles in v1 (`trino` added by Amendment A, R7): `trino`, `bigquery`, `snowflake`, `redshift`, `postgres`, `duckdb`,
   `databricks` (Spark SQL), `clickhouse`, `tsql` (SQL Server/Synapse/Fabric),
   `mysql` (also MariaDB), `oracle`.
2. **sqlglot bridge** (extra `factlang[sqlglot]`): converts the internal AST to a
   `sqlglot` expression tree and calls `.sql(dialect=<name>)` for any sqlglot
   dialect name (e.g. `trino`, `presto`, `athena`, `teradata`, `hive`, `starrocks`,
   `doris`, `exasol`, `dremio`…). Params are emitted as sqlglot placeholders mapped
   to the target paramstyle.

**Resolution order** for `dialect="x"`: native profile if one exists; else sqlglot
if installed; else `FL504 unknown dialect` listing native names and the hint
`pip install factlang[sqlglot]`. `Checker(..., prefer="sqlglot")` forces the bridge
(used by tests to cross-check).

### 8.4 Identifiers and literals (safety)
- **The governing rule: a claim identifier means exactly what the same identifier
  would mean written in that warehouse's SQL.**
  - An *unquoted* claim identifier is emitted **unquoted**, so the warehouse applies
    its own case and collation rules (including configuration-dependent ones such
    as MySQL `lower_case_table_names` or T-SQL collation) — FactLang never
    second-guesses them. Exception: if the name is in the profile's
    `reserved_words`, it is emitted quoted after applying `unquoted_case`
    (Snowflake/Oracle `upper`; Postgres/Redshift `lower`; all others `preserve`),
    which is what the unquoted form would have resolved to.
  - A *quoted* claim identifier (`"Order Items"`, `` `x` ``) is emitted quoted with
    the profile's quote character, inner quotes doubled, case preserved.
  - Unquoted identifiers already match `[A-Za-z_][A-Za-z0-9_$]*` by the lexer.
    Any identifier containing NUL or newline, or longer than
    `max_identifier_length`, is refused (`FL502`), never sanitised.
- **Every literal is a typed bind parameter.** Paramstyle is a **render-time input,
  not a profile field**, because one dialect has drivers with different styles.
  `RenderedProbe` = `sql_template` (neutral placeholders), `params`
  (ordered tuple of `Param(name, value, family)`), `arity`, `plan`, `dialect`,
  `tier`; `probe.bind(paramstyle) -> (sql, params)` produces the driver form for
  `qmark`/`numeric`/`named`/`format`/`pyformat`; each connector declares its
  paramstyle and calls `bind`. `fl.compile(...)` therefore works with no connector.
  `probe.inline()` produces a display-only SQL string with literals inlined and
  escaped, used for `explain` and evidence, never executed.
- Rendered probes are `SELECT` only. A guard asserts the rendered text begins with
  `SELECT`/`WITH` and contains no `;` outside literals.

### 8.5 Tiers
- `verified` — native profile executed against a real engine in CI: `trino` (Amendment A, `trinodb/trino` service container), `duckdb`,
  `postgres`, `mysql`, `clickhouse`, `tsql` (GitHub Actions service containers).
- `native` — native profile whose SQL is checked in CI by parsing it with sqlglot in
  that dialect and by golden structural tests, but not executed in CI because it
  needs a cloud account: `bigquery`, `snowflake`, `redshift`, `databricks`,
  `oracle`. A `tests/live/` suite runs them when credentials are present
  (opt-in, `FACTLANG_LIVE_<DIALECT>=1`).
- `best_effort` — any dialect served by the sqlglot bridge.
Tier is reported on every `Outcome`.

---

## 9. Execution, catalogs and scale

### 9.1 Connectors
```python
class Connector(Protocol):
    dialect: str                       # profile name
    paramstyle: str                    # driver paramstyle; the connector binds the probe itself
    def execute(self, probe: RenderedProbe, *, timeout_s: float | None) -> QueryResult: ...
class QueryResult: rows: list[dict[str, Any]]; bytes_processed: int | None
```
Optional async: `AsyncConnector.aexecute`. Adapters (each an extra, imported lazily):
`DBAPIConnector(conn, dialect)` (any PEP 249 connection — covers psycopg, duckdb,
snowflake-connector, redshift_connector, databricks-sql, pymysql, oracledb, pyodbc,
clickhouse-connect's DB-API), `SQLAlchemyConnector(engine)` (dialect inferred from
`engine.dialect.name`), `BigQueryConnector(client, project, dataset?,
maximum_bytes_billed?)` (uses query parameters and dry-run cost check),
`CallableConnector(fn, dialect)` for hosts that run SQL themselves.
`factlang.connect(dsn)` builds a connector from a SQLAlchemy URL when SQLAlchemy is
installed. `TrinoConnector` is covered by `DBAPIConnector` over the `trino` driver
(extra `trino`, Amendment A).

**Metadata SQL (Amendment A, R8).** `fl.connectors.run_metadata(connector, sql, params=(),
*, timeout_s=None) -> QueryResult` executes host-authored **catalog metadata** SQL
(system views, `INFORMATION_SCHEMA`, `SHOW …`, `DESCRIBE …`) through any connector, using
the same execution, timeout, paramstyle binding and error mapping (`FL700`/`FL702`) as
probes. It is for libraries built on FactLang that must list schemas/tables; it is not a
claim and produces no `Outcome`. Guard (raises `ExecutionError("FL703 …")` before
executing): one statement, no `;` outside string literals, and the first keyword is one of
`SELECT WITH SHOW DESCRIBE DESC EXPLAIN` (not `EXPLAIN ANALYZE`). The guard is **lexer-free and deliberately over-strict**: it scans the raw text (quotes ignored, `{{name}}` parameter tokens exempt) and refuses any `;`, `->>`, `--`, `/*`, `*/`, `//`, `#`, `$`, `\`, Oracle `q'` quoting, and any write/DDL/control keyword as a whole word anywhere (`INTO`, `COPY`, `CALL`, `EXEC`, `MERGE`, `GRANT`, `SET`, `USE`, `ATTACH`, `PRAGMA`, … and all DML/DDL). Any non-ASCII character outside parameter tokens is refused (engines such as Spark fold Unicode case into keywords). On `tsql`, where statements need no separator, only `SELECT` is accepted and it runs wrapped as `SELECT * FROM (<sql>) AS fl_meta_q`; quote characters (`'` `"` `[` `]`) are refused and parentheses must balance, so the wrapper cannot be escaped. Values must be passed as bound parameters. Modelling each dialect's lexer was rejected after review found bypasses through dollar quotes, MySQL `--`, Snowflake `//` and Oracle `q'`. The guard is a keyword guard, not a sandbox: the connection's credentials are the real security boundary. Params are bound, never spliced.

### 9.2 Catalogs
```python
class Catalog(Protocol):
    def table_exists(self, ref: TableRef) -> bool | None: ...      # None = unknown
    def columns(self, ref: TableRef) -> dict[str, str] | None: ...  # name -> type
```
`NullCatalog` (skip name checks), `StaticCatalog` (dict / JSON file),
`WarehouseCatalog(connector)` (in `connectors/catalog.py`; per-table `INFORMATION_SCHEMA` query, only for tables
referenced by the claims being checked — **never enumerates schemas**),
`CachedCatalog(inner, ttl_s, max_tables)` (LRU). Unknown (`None`) is not an error:
the analyser emits `FL311` (no such column) only on a definite `False`/missing key,
and an `FL313` info when it could not check.

### 9.3 Optimizer and engine
- De-duplicate claims with the same semantic hash in a batch.
- **Fuse** `ColumnAggregate`s (and `CompositeKeyAggregate`s) with the same table and
  same filter into one `SELECT` with all metrics (aliases prefixed `m<i>__`), then
  split results back per claim. Never fuse across filters. Fusion is on by default,
  `fuse=False` disables.
- `Checker(connector, *, pack=standard, catalog=None, concurrency=4,
  timeout_s=60, max_bytes=None, fuse=True)`; `check(text) -> Report`;
  `acheck(text)` async; `Report` = ordered `Outcome`s + summary counts + `ok` bool (no
  FAIL/ERROR/INVALID and no ERROR diagnostic) + `verified` bool (at least one outcome is not
  UNPROBEABLE and every such outcome is PASS — an empty or all-UNPROBEABLE report is not
  verified; agents should read `verified`) +
  `to_dict()/to_json()/to_markdown()`.
- Cost guard: when the connector supports dry-run (BigQuery) and `max_bytes` is set,
  a probe over budget is `ERROR` with `FL701 over cost budget`, never executed.
- **Raw measurement (Amendment A, R1).** `Checker.measure(plans: Sequence[ProbePlan])
  -> list[Measurement]` (and `ameasure`) runs IR primitives that have no claim, through the
  same render → (cost guard) → execute path, with fusion and de-duplication across the
  batch (identity = the plan's canonical form). `Measurement(plan, payload, verdict_hint,
  evidence, error)`: `payload` is exactly what `judge.run.payload_for` would hand a
  contract (`SINGLE_ROW` → the row dict in wire-format aliases; `ROW_SET` →
  `{"rows": [...]}`; no rows → `None`); `evidence` is the same `Evidence` an `Outcome`
  carries; `error` is `None` or a `Diagnostic` (`FL700`/`FL701`/`FL702`). It never raises
  for execution failures. `fl.measure(plan_or_plans, *, connector, **checker_kwargs)` is
  the one-call form.
- **Cost estimate (Amendment A, R5).** `Checker.estimate(items: str | Sequence[ProbePlan])
  -> list[CostEstimate]` renders claims (text) or plans and asks the connector's optional
  dry-run for bytes **without executing**. `CostEstimate(semantic_hash_or_plan_key,
  sql, bytes: int | None, method: "dry_run" | "unknown")`; connectors without dry-run give
  `bytes=None, method="unknown"` — never a guess.

---

## 10. Public API, CLI and agent surface

```python
import factlang as fl

# 1. Front end only (no warehouse)
doc = fl.parse("JOIN a.x -> b.y MATCH >=99%")        # Document + diagnostics
print(fl.lint(text).render())                          # caret diagnostics
print(fl.format(text))                                 # canonical form
(probe,) = fl.compile("UNIQUE orders.id", dialect="snowflake") # list[RenderedProbe]; sql + params

# 2. One call
outcome = fl.check("UNIQUE orders.id", connector=fl.DBAPIConnector(conn, "duckdb"))

# 3. Batch
checker = fl.Checker(connector, catalog=fl.WarehouseCatalog(connector))
report = checker.check(open("facts.fl").read())

# 4. Extend
pack = fl.standard_pack().extend("mine", "1.0")
@pack.verb("POSITIVE", operands=[fl.Operand.column()], filterable=True)
def positive(claim):                                    # returns the plan
    return fl.ir.ColumnAggregate(claim.table, claim.column, [fl.Metric.MIN_VALUE],
                                 filter=claim.filter)  # a filterable verb must pass it on (FL605)
@positive.derive
def _(payload, claim): return {"min": payload["min_value"]}
positive.decide(fl.Compare("min", ">", value=0))
positive.example("POSITIVE orders.amount", valid=True)
```

**Amendment A additions (R1, R5, R6):**
```python
m = fl.measure(fl.ir.ValueMembership(t, col, declared=(), report_limit=20), connector=c)  # exact top-k
est = checker.estimate("JOIN orders.customer_id -> customers.id MATCH >=99%")             # dry-run bytes
f, diags = fl.parse_filter("deleted IS NULL OR NOT deleted")     # (FilterExpr | None, DiagnosticBag), no claim
frag = fl.sql.render_filter(f, table=t, dialect="snowflake")     # RenderedFragment(sql_template, params)
snippet = fl.sql.render_join([edge1, edge2], dialect="bigquery",
                             filters={t1: f})                     # RenderedFragment, display/bindable
print(frag.inline())                                              # display-only text, literals escaped
```
`fl.parse_filter(text, *, catalog=None, table=None) -> tuple[FilterExpr | None,
DiagnosticBag]` parses and analyses a bare §4.5 condition (no `WHERE` keyword needed;
accepted with it too). `RenderedFragment` has the same `sql_template` placeholders,
`params`, `bind(paramstyle)` and `inline(...)` as `RenderedProbe` but is **not** a full
statement and is never executed by FactLang: `render_filter` produces a boolean expression
qualified with an optional alias; `render_join(edges, dialect, *, filters=None,
aliases=None)` produces `FROM a JOIN b ON … [AND …]` for a path of `EdgeRef`s (composite
edges allowed; `CAST` honoured), with each table's filter ANDed into its `ON`/`WHERE`
per the documented rule (left-most table's filter in `WHERE`, others in their `ON`).
Both go through the same internal SQL AST and identifier rules (§8.2, §8.4), on native
profiles and the sqlglot bridge.

Extension operations on `ContractPack`: `register(c)` (raises on duplicate),
`override(c)` (replaces, records the replaced version), `alias("CNT", "COUNT")`,
`remove(name)`, `extend(name, version)` (copy-on-write child pack),
`merge(other, on_conflict="error"|"keep"|"replace")`. Packs are discoverable via the
entry-point group `factlang.packs`. Custom value types:
`fl.types.register(ValueType("EMAIL_DOMAIN", accepts=frozenset({LiteralKind.STRING}), coerce=fn, validate=check))`. Custom dialect:
`fl.sql.register_profile(DialectProfile(...))` or subclass an existing one with
`dataclasses.replace`. Custom primitive: `fl.ir.register_primitive(cls, lower=fn)`.

**CLI** (`factlang`, stdlib `argparse`): `lint FILE…`, `fmt [--check] FILE…`,
`explain FILE --dialect D` (prints SQL), `check FILE --dsn URL | --dialect D
--connector module:attr`, `verbs [--json]`; `--format text|json|markdown`;
exit codes 0 ok, 1 a claim FAILED, 2 invalid input, 3 errors/undecidable.

**Agent surface** (`factlang.agent`):
- `tool_definitions(pack)` → JSON-schema tool specs (`factlang_check`,
  `factlang_lint`, `factlang_explain`) usable with any function-calling LLM API.
- `verb_reference(pack, style="markdown"|"compact")` → prompt-ready reference
  generated from each contract's spec + examples (cannot drift).
- `handle_tool_call(name, args, checker)` → JSON result.
- `factlang.agent.mcp` (extra `factlang[mcp]`): an MCP server exposing those tools
  over stdio, `factlang mcp --dsn …`.

---

## 11. Distribution

Extras: `sqlglot`, `sqlalchemy`, `bigquery`, `snowflake`, `redshift`, `databricks`,
`postgres`, `duckdb`, `mysql`, `clickhouse`, `mssql`, `oracle`, `trino` (`trino>=0.330`,
Amendment A), `mcp`, `all`, `dev`, `docs`. **Public-API stability (Amendment A, R10):**
`factlang.ir` primitives and their fields/result columns, `Checker.measure`/`estimate`,
`fl.measure`, `fl.parse_filter`, `fl.sql.render_filter`/`render_join`,
`RenderedFragment`, `fl.connectors.run_metadata` are public API under SemVer: no breaking
change within a `0.x` minor series. Version from `factlang/__about__.py`, SemVer, starting `0.1.0`; the language
itself has a `LANGUAGE_VERSION = "1.0"` independent of package version.

---

## 12. Diagnostics

Codes `FLnnn`, ranges by stage: `FL1xx` lexer, `FL2xx` parser, `FL3xx` analyser,
`FL4xx` planning, `FL5xx` rendering, `FL6xx` contract completeness (test-time),
`FL7xx` execution. Each `Diagnostic`: `code`, `severity` (error/warning/info),
`message`, `span` (line, col, end_col), `hint`, `suggestion` (did-you-mean),
renderable as caret text and as JSON. The authoritative table lives in
`docs/reference/diagnostics.md` and a test asserts it equals the set of codes the
source emits. Initial set (builder extends as needed, keeping ranges):
FL101 unterminated string/quoted identifier · FL102 unexpected character ·
FL201 expected a value · FL202 unclosed list · FL203 unexpected token ·
FL204 duplicate attribute · FL205 dangling continuation · FL206 malformed filter · FL207 nesting too deep (limit 64) ·
FL300 unknown verb (suggestion) · FL301 wrong operand count · FL302 wrong operand
kind / ref depth · FL303 unknown attribute (suggestion) · FL304 missing required
attribute · FL305 wrong value type · FL306 operator not allowed · FL307 unusable
column list · FL308 filter not allowed for verb · FL309 invalid value (range, e.g.
ratio > 1) · FL311 no such table/column · FL312 cross-type join without CAST
(warning) · FL313 could not verify names (info) · FL500 primitive not renderable ·
FL501 metric not renderable (reserved; not emitted in 0.1.0) · FL502 identifier not representable · FL503 cast not
expressible · FL504 unknown dialect · FL505 too many table-name parts for dialect · FL600 decide reads a fact derive never
produces · FL601 decide reads an undeclared attribute · FL602 contract has fewer than 3 valid + 3 invalid
examples · FL603 an example does not behave as declared (or its plan/catalog function raises) · FL604 incomplete contract (decorator builder missing a part) · FL605 a filterable verb's plan drops the claim's WHERE filter · FL700 execution failed · FL701 over cost budget · FL702 timeout · FL703 metadata SQL refused by guard (Amendment A) · FL404 sampled plan in a claim (Amendment B) · FL405 sample method needs a single read; use keyhash (Amendment B) · FL506 sample method not supported by dialect (Amendment B).
`FL5xx` raise `RenderError`; `FL6xx` are asserted by tests over every registered
pack; `FL7xx` become `ERROR` outcomes.

---

## 13. Testing strategy

- **Grammar conformance**: `docs/reference/grammar.md` (normative) contains fenced
  `factlang-valid` / `factlang-invalid <CODE>` blocks; a test extracts and runs them.
- **Contract completeness** (FL6xx) over every contract in every installed pack.
- **Example tests**: every `Example` of every contract parses/fails as declared.
- **Property tests** (`hypothesis`): lexer/parser/analyser never raise on arbitrary
  text; `format` is idempotent and hash-preserving; percent/number normalisation.
- **Dialect conformance**: every primitive × every metric × every native profile
  renders; aliases match the wire format; no `LIMIT`/`TOP`/`SAMPLE`/`TABLESAMPLE`;
  NULL-before-group rule present; rendered SQL parses with sqlglot in that dialect;
  native vs sqlglot path produce semantically equal results on DuckDB and Postgres.
- **Execution tests** on real engines (tier `verified`) using seeded fixture tables
  with known answers, including NULL-heavy, empty and duplicate-key tables; each
  standard verb has a PASS, a FAIL and a NO_DATA case per engine.
- **Layering** and **portability** (core stdlib-only) AST tests.
- **Coverage gate** 90% on core.
- Live cloud tests (`tests/live/`) opt-in only.

---

## 14. Repository, CI, docs, Claude scaffolding

### 14.1 Repository layout
```
factlang/
  src/factlang/…              (§5.1)
  tests/{unit,conformance,integration,live,property}/
  docs/                        mkdocs-material site source
    index.md, getting-started.md, guide/*, reference/{grammar,verbs,diagnostics,api}.md,
    extending/{verbs,dialects,connectors,catalogs,packs}.md, agents.md, specs/
  site-playground/             in-browser playground (Pyodide)
  .github/workflows/{ci,release,docs,wiki}.yml
  .claude/{agents,skills}/…   (§14.4)
  CLAUDE.md AGENTS.md MEMORY.md README.md LICENSE CONTRIBUTING.md CHANGELOG.md
  CODE_OF_CONDUCT.md SECURITY.md pyproject.toml
```

### 14.2 CI (GitHub Actions)
- `ci.yml` (push + PR): ruff check, ruff format --check, mypy --strict, unit +
  property + conformance on Python 3.10–3.13 × ubuntu plus Windows 3.13 per push (the full
  3.10–3.13 × ubuntu/macos/windows matrix runs nightly, on dispatch and on tags),
  integration job with service containers (postgres, mysql, clickhouse, mssql, trino; pinned tags) +
  duckdb, coverage gate, `python -m build`, `twine check`, upload sdist+wheel as
  workflow artifacts (downloadable from every run).
- `release.yml` (tag `v*`): build, attach wheel+sdist to a GitHub Release with
  generated notes, and publish to PyPI via Trusted Publishing (owner registers a
  pending publisher: project `factlang`, owner `rupakc`, repo `factlang`, workflow
  `release.yml`, environment `pypi`; the workflow job uses `environment: pypi` and
  `permissions: id-token: write`).
- `docs.yml` (main): build mkdocs site + playground, deploy to `gh-pages`.
- `wiki.yml` (main, `docs/wiki/**` changed): sync `docs/wiki/` to the GitHub wiki
  repo. Note: GitHub creates the wiki git repo only after the first page exists, so
  the first wiki page must be created once (via web UI or the first manual push).

### 14.3 Documentation
- **README**: what/why in 10 lines, 30-second example, install matrix, verb table,
  dialect/tier table, links.
- **GitHub Wiki** (`docs/wiki/`, synced): Home, Concepts (claims, verdicts, evidence,
  tiers), Architecture (pipeline diagram, layers), Language Guide, each component
  (Lexer, Parser, Analyzer, IR, Dialects, sqlglot bridge, Optimizer, Connectors,
  Catalogs, Judge), Standard Verbs, Extending (one page per extension point),
  Agent Integration, CLI, Diagnostics, FAQ, Glossary, Contributing, Design
  Decisions (the 14 lineage concepts with their reasons). Plain language; every page
  starts with a 3-line summary and ends with "see also".
- **gh-pages site**: landing page (problem → claim → verdict animation, "where it
  shines" use cases: agent answer verification, schema-registry validation,
  data-contract CI, migration checks), interactive **playground** running the pure-
  Python core in Pyodide (lint, format, explain SQL for any native dialect — no
  warehouse needed), API docs (mkdocstrings), guides. Live execution in the browser
  (e.g. DuckDB-WASM) is **builder's call after a feasibility spike**; the playground
  must not depend on it.

### 14.4 Claude scaffolding
- `CLAUDE.md` — project rules: layers, stdlib-only core, no sampling, aliases are
  wire format, every verb one file with examples, commands, EGM workflow.
- `AGENTS.md` — same rules for non-Claude agents + the roles below.
- `MEMORY.md` — durable decision log (dated entries: decision, why, how to apply).
- `docs/specs/` — Elephant specs and plans (this file and the plan).
- `.claude/agents/`: `goldfish-implementer` (implements one plan task from spec +
  task only), `goldfish-spec-reviewer` (reads a spec cold, lists what it cannot
  build from it), `compiler-reviewer` (grammar/parser/analyser correctness and
  recovery), `dialect-engineer` (profiles, conformance), `docs-writer`,
  `release-engineer`.
- `.claude/skills/`: `add-verb`, `add-dialect`, `add-connector`,
  `run-conformance`, `release`, `egm-handoff` (how to cut a task for a goldfish).

---

## 15. Process: Elephant-Goldfish

- **Elephant** (the long-running session): owns this spec, the plan
  (`docs/specs/2026-11-24-factlang-plan.md`) and `MEMORY.md`. Writes no library code.
- **Goldfish spec check**: before approval, a fresh agent reads this spec cold and
  lists everything it could not implement without asking. The Elephant resolves
  each item in the spec (or asks the owner).
- **Goldfish implementers**: each plan task goes to a fresh agent that receives only
  this spec, the plan task, and the repo. A separate fresh reviewer checks the diff
  against the spec. Independent tasks run in parallel (fan-out); tasks touching the
  same layer run sequentially.
- If a goldfish needs information not in the spec, that is a spec defect: the
  Elephant amends the spec and records it in `MEMORY.md`.
- Commits land incrementally on `main`, each green in CI.

### 15.1 Milestones (order of work)
1. Repo skeleton, license, pyproject, CI lint/test/build, Claude scaffolding.
2. Diagnostics, tokens, lexer, AST, parser, formatter (+ grammar doc, property tests).
3. Types, signatures, catalog, analyser, identity.
4. IR, judge, contract/pack + decorator API.
5. Standard pack (verbs in parallel).
6. SQL AST, lowering, native printer, 10 profiles, conformance.
7. sqlglot bridge + cross-check tests.
8. Connectors, optimizer, engine, integration CI with service containers.
9. Public API, CLI, agent tools, MCP server.
10. Docs site + playground, wiki, release workflow, v0.1.0.

---

## 16. Approaches considered

1. **Hand-written SQL per warehouse** (the predecessor's approach). Precise and
   dependency-free, but ten full renderers drift on NULL/case semantics. Rejected
   as the sole mechanism.
2. **One shared generator + small dialect profiles**. Dependency-free core,
   cheap new dialects, one conformance suite.
3. **sqlglot transpilation**. Widest coverage, but a core dependency whose
   translation bugs land exactly where a fact checker cannot be wrong.

**Chosen (owner): 2 + 3 combined.** Native profiles are the core and the verified
path; sqlglot is an optional extra covering the long tail, labelled `best_effort`,
and used in tests to cross-check native SQL. Both start from the same internal SQL
AST (§8.2) so they cannot disagree on semantics.

Also decided by the owner: build from scratch, reusing the predecessor's concepts, not its code.

## 17. Left to the builder
Exact internal class names below the public API; error message wording (codes and
ranges are fixed); the profile field set beyond §8.3 if a dialect needs more; docs
site theme details; whether the playground can execute queries in-browser.

---

## 18. Amendment A — requirements from SchemaGuru (2026-11-24, owner-directed)

**Context.** SchemaGuru (`github.com/rupakc/schemaguru`, spec
`~/schemaguru/docs/specs/2026-11-24-schemaguru-design.md` §5.2) is an open-source schema
intelligence library that **depends on FactLang** for all measurement SQL, dialect
knowledge, connectors and verdicts (owner decision). A cold builder check of its spec
found capabilities FactLang v1 did not provide. The owner directed (2026-11-24) that they
be added to **FactLang 0.1.0** with immediate effect, so SchemaGuru never renders
measurement SQL itself or depends on FactLang internals. Each item is specified inline
in the section named; this table is the index and the acceptance list.

| # | Requirement | Specified in | Acceptance (tests) |
|---|---|---|---|
| R1 | Run IR primitives with no claim: `Checker.measure`/`ameasure`, `fl.measure`, `Measurement` | §9.3, §10 | DuckDB: each primitive measured returns the same payload a claim's derive receives; fused batch == unfused; failure → `error` set, no raise |
| R2 | `ValueMembership(declared=())` allowed → exact top-k observed values; `report_limit` ≤ 1000 | §8.1 | DuckDB: top-k equals `GROUP BY … ORDER BY count DESC, value ASC` reference; NULL excluded; `VALUES t.c IN []` claim behaviour unchanged |
| R3 | Composite join keys: `KeyOverlap` key tuples; `JOIN … WITH [..] TO [..]` | §6.3, §8.1 | parser/analyser: FL304 when one of WITH/TO missing, FL309 on length mismatch; DuckDB: a 2-part key with a NULL part excluded; examples ≥3 valid/≥3 invalid |
| R4 | Reverse & row facts: `coverage`, `row_match_rate`, `fan_in`, `distinct_right`, `left_non_null`, `matched_left_rows`; `COVERAGE` attribute; IR-only `right_filter` | §6.3, §8.1 | DuckDB fixture: asymmetric join (forward 1.0, coverage < 0.01) gives exact numbers; `None` (not 0) on an empty right side |
| R5 | Price without executing: `Checker.estimate` → `CostEstimate` via the connector's optional dry-run | §9.3 | fake dry-run connector returns bytes; non-dry-run connector → `bytes=None, method="unknown"`; nothing executed (connector call log) |
| R6 | Public `fl.parse_filter`, `fl.sql.render_filter`, `fl.sql.render_join`, `RenderedFragment` | §10 | every native profile renders; hostile literal only in params; DuckDB executes `SELECT COUNT(*) FROM <render_join>` and matches a hand-written join |
| R7 | Native `trino` profile, tier `verified` (Trino service container in integration CI) | §4.3, §8.3, §8.5, §11 | conformance suite parametrised over profiles includes trino; Task-20 verb matrix runs on Trino |
| R8 | `fl.connectors.run_metadata` for catalog metadata SQL through any connector, guarded (`FL703`) | §9.1 | guard rejects DML/DDL/multi-statement; DuckDB `SELECT … FROM information_schema.tables` works; `SHOW TABLES` allowed |
| R9 | Bare boolean filter atom (`deleted`, `NOT deleted`) | §4.5 | grammar conformance blocks; hash(`deleted`) == hash(`deleted = TRUE`); FL305 on a non-boolean column when catalog knows |
| R10 | Public-API stability promise for the items above | §11 | API docs list them; `tests/unit/test_api.py` asserts exports |

New diagnostic: **FL703** metadata SQL refused by guard (execution range). Add it to §12's
table and `docs/reference/diagnostics.md`.

**Ordering.** These ship in 0.1.0. Items touching already-completed tasks (R9: parser)
are a new plan task executed immediately; the rest are folded into the not-yet-started
plan tasks (see the plan's "Amendment A" notes). SchemaGuru pins `factlang>=0.1,<0.2` and
develops against FactLang `main` until 0.1.0 is on PyPI.

---

## 19. Amendment B — sampled measurement for very large tables (2026-11-24, owner-directed)

**Context.** The owner ruled that SchemaGuru must measure terabyte-scale tables on a
*representative sample* (small tables stay exact). SchemaGuru renders no SQL itself, so
FactLang provides sampling — **for raw measurement only**. The anti-goal stands: no
verdict (PASS/FAIL/NO_DATA) is ever produced from sampled data; claims, contracts and the
analyser never produce a sampled plan. `LIMIT` is never a sampling method.

**B1 — `SampleSpec` (IR, layer 8).**
```python
@dataclass(frozen=True)
class SampleSpec:
    method: Literal["keyhash", "system", "bernoulli"]
    fraction: Decimal          # 0 < fraction <= 1 (e.g. Decimal("0.01"))
    seed: int = 0              # deterministic; same seed + same data => same sample
    key: tuple[Ident, ...] = ()  # required for keyhash (columns hashed), empty otherwise
```
Primitives `ColumnAggregate`, `CompositeKeyAggregate`, `ValueMembership`, `KeyOverlap`,
`PatternCoverage` gain `sample: SampleSpec | None = None`. `KeyOverlap` has
`sample` (left) and `right_sample` (right); with `keyhash` on both sides using the same
`fraction` and `seed`, both sides keep exactly the same key values (the keyhash predicate
depends only on the key value, the seed and the fraction). `MetadataQuery` is never sampled.

**FL405 ruling (2026-11-24, builder; SchemaGuru Elephant agreed).** `system` and `bernoulli` samples are allowed only on plans that lower to a single read. `ValueMembership`, `KeyOverlap`, and `ColumnAggregate`/`CompositeKeyAggregate` that request a keyed metric (`MAX_PER_KEY`, `DUPLICATE_KEYS`, composite `DISTINCT_KEYS`) read the sampled table more than once and a block/row sample is not guaranteed to repeat across reads (e.g. BigQuery re-evaluates CTEs), so lowering raises **FL405** "sample method needs a single read; use keyhash" (planning range). `keyhash` is deterministic and allowed everywhere.

**B2 — rendering (layer 12).** `DialectProfile` gains `sample_styles: Mapping[str, str |
None]` per method and a `keyhash_expr` (deterministic 64-bit hash of the cast-to-string
key parts, seeded, mapped to `[0, 1_000_000)`; predicate `bucket < fraction * 1_000_000`).
Lowering adds the sample to the `FROM` item (block/Bernoulli: dialect's
`TABLESAMPLE SYSTEM|BERNOULLI (p PERCENT) [REPEATABLE(seed)]` / Snowflake `SAMPLE
SYSTEM|BERNOULLI (p) SEED (seed)` / DuckDB `USING SAMPLE p% (system|bernoulli, seed)` /
Oracle `SAMPLE BLOCK (p) SEED (seed)` / `SAMPLE (p)` etc.) or as a `WHERE` predicate
(keyhash: e.g. BigQuery `MOD(ABS(FARM_FINGERPRINT(CONCAT(CAST(seed AS STRING), '|',
CAST(k AS STRING)))), 1000000)`, Postgres/Redshift via `hashtext`/`FNV_HASH`-class
functions, ClickHouse `cityHash64`, Snowflake `HASH`, DuckDB `hash`, Trino
`xxhash64(to_utf8(...))`). Exact per-profile spellings are the builder's, pinned by
conformance tests — **except `keyhash`, whose expression is fixed below (ruling
2026-11-24).**

*Keyhash ruling.* Consistency is required **within one dialect only** (both sides of a
join live in the same warehouse); cross-dialect equality of samples is a non-goal. Let
`s` = the hash input string: `<seed as decimal text> || '|' || CAST(k1 AS <string type>)
[|| '|' || CAST(k2 …)]…` (rows with a NULL key part are excluded before hashing, as for
every composite key), with the seed bound as a string parameter. Let `B = 1000000`. For a
*signed* hash `h`, the bucket is `MOD(MOD(h, B) + B, B)` (never `ABS(h)`, which overflows on
the minimum 64-bit value). The row is kept iff `bucket < fraction × B` (fraction × B bound
as an integer parameter, `ROUND` half-up).

| Profile | `h` (or bucket) | Notes |
|---|---|---|
| bigquery | `FARM_FINGERPRINT(s)` (signed INT64) | |
| snowflake | `HASH(s)` (signed 64-bit) | |
| databricks | `xxhash64(s)` (signed BIGINT) | |
| duckdb | bucket = `hash(s) % 1000000` | `hash` is UBIGINT; no sign fold needed |
| clickhouse | bucket = `cityHash64(s) % 1000000` | UInt64 |
| postgres | `hashtextextended(s, 0)` (signed BIGINT) | PG ≥ 11 |
| redshift | `STRTOL(SUBSTRING(MD5(s), 1, 15), 16)` | 60-bit, non-negative |
| mysql | `CAST(CONV(SUBSTRING(MD5(s), 1, 15), 16, 10) AS UNSIGNED)` | 60-bit, non-negative |
| tsql | `CONVERT(BIGINT, SUBSTRING(HASHBYTES('MD5', CAST(s AS NVARCHAR(4000))), 1, 8))` | signed; `s` cast to NVARCHAR so VARCHAR and NVARCHAR keys hash alike |
| oracle | `ORA_HASH(s, 4294967295, 0)` | 32-bit, non-negative; seed lives in `s` |
| trino | `from_big_endian_64(xxhash64(to_utf8(s)))` | signed BIGINT |
| sqlglot bridge | FL506 for `keyhash` unless the target is one of the above | |

Tests (Task 14/15/20): per profile the rendered predicate structurally matches this table;
on every `verified` engine (incl. trino) a join sampled with keyhash on both sides keeps
identical key sets, and re-running with the same seed keeps the same rows.

A method the dialect cannot express → **`FL506` sample method not
supported by dialect** (RenderError) — never a silent exact or silent `LIMIT`. Fraction 1
renders no sample. The sqlglot bridge supports `system`/`bernoulli` where sqlglot has
`TABLESAMPLE`, and `keyhash` only when the bridge has a hash mapping; otherwise FL506.

**B3 — results.** A sampled `Measurement.evidence` carries `sample` (method, fraction,
seed, key) and `method = "sampled:<method>:<fraction>:<seed>"`; exact measurements carry
`method = "exact"`. Payloads are the **raw metrics on the sampled rows** (FactLang does not
scale or estimate — the host does) plus `sampled_rows` (rows in the sample; for
`KeyOverlap` per side: `sampled_left_rows`, `sampled_right_rows`). `Checker.estimate`
prices sampled plans (dry-run bytes reflect block sampling where the warehouse does).

**B4 — guards and tests.** `Checker.check`/contracts reject a plan with `sample` set
(`FL404 sampled plan in a claim`, planning range — a contract bug, surfaced as ERROR).
Conformance: every profile renders each method it declares or raises FL506; sample text
appears only in sampled plans; DuckDB execution: (a) keyhash on both sides of a join with
the same seed keeps identical key sets; (b) keyhash on a key keeps all rows of each kept
key; (c) same seed ⇒ same sample on re-run; (d) fraction 1 ⇒ identical to exact;
(e) `system`/`bernoulli` row counts within a binomial tolerance on a 1M-row table.
New codes: **FL404**, **FL506**. Ships in 0.1.0 with Amendment A; covered by R10's
stability promise.
