# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The language has its own version
(`LANGUAGE_VERSION`), independent of the package version.

## [Unreleased]

## [0.1.4] - 2026-11-25

A single inline-output fix. Bound-parameter SQL is unchanged.

### Fixed (inline output)
- `databricks` (native profile), and bridged `spark` and `spark2`, no longer inline 0.1.3's
  zone-less `TIMESTAMP 'YYYY-MM-DD HH:MM:SS[.ffffff]'`, which named a different instant than
  the UTC value: Databricks and Spark 3.0+ read a zone-less literal in
  `spark.sql.session.timeZone`, Spark 2.x in the JVM default zone.
  - `databricks`, `spark`: `TIMESTAMP 'YYYY-MM-DD HH:MM:SS[.ffffff] UTC'`, the Trino
    spelling.
  - `spark2`: `CAST('YYYY-MM-DD HH:MM:SS[.ffffff]Z' AS TIMESTAMP)`. Spark 2.x's
    `TIMESTAMP '…'` literal goes through `java.sql.Timestamp.valueOf` and rejects a zone
    suffix; the string-to-timestamp cast reads the `Z` as UTC.
  - `hive`, `teradata` and `exasol` keep the zone-less form: their `TIMESTAMP` is a
    zone-less (local date-time) type, so a naive UTC wall-clock is the correct value.

  Display/inline SQL only; quote-doubling decisions are unchanged (`spark`/`spark2` still
  refuse a `'`).

## [0.1.3] - 2026-11-25

Compatible improvements: two quoting helpers, a claim cap for agents, per-dialect inline
spellings, and guards that hold under `python -O`. Bound-parameter SQL is unchanged.

### Added
- `factlang.syntax.needs_quoting(name)` and `factlang.syntax.quote_name(name)` (stable API):
  whether a name must be quoted to be written in FactLang source, and its double-quoted
  form. `quote_name` raises `ValueError` for an empty name or one holding CR, LF or NUL,
  and `TypeError` for an argument that is not a `str`.
- `fl.ir.unregister_primitive(cls)` (`KeyError` if `cls` is not registered), the supported
  way to remove a registration. `fl.ir.register_primitive` and `unregister_primitive` are
  listed in the stable API; `fl.ir.PRIMITIVE_LOWERINGS` is read-only by convention (it stays
  a `dict` in 0.1.x; a read-only view is planned for 0.2.0).
- Agent tools: `factlang_check` / `factlang_explain` (`handle_tool_call` and the MCP server)
  refuse a call carrying more than `max_claims` claims with the new error code
  `too_many_claims`, before any analysis or SQL. The default is
  `factlang.agent.tools.DEFAULT_MAX_CLAIMS` (200); it is set by `factlang mcp
  --max-claims N` and by a `max_claims=` keyword on `handle_tool_call` and on the MCP
  module's `call_tool`, `build_server` and `serve` (`None` removes the cap). New
  `factlang.agent.tools.count_claims` (CRLF, CR and LF all end a line). `factlang_lint` is
  not capped. Tool error codes may be added in patch releases; consumers must handle codes
  they do not know.
  **Behaviour change for existing callers:** the 200 default also applies to code that
  already calls `handle_tool_call` without the keyword, so a call with more than 200
  claims now returns `too_many_claims`; pass `max_claims=None` to restore the old
  behaviour.
- Warning `FL314`: a `TOLERANCE` or `ABS_TOLERANCE` on a `MIN`/`MAX` whose `VALUE` is a
  date or timestamp has no effect, and the claim will be `error` when judged (judging is
  unchanged; as a warning it leaves `factlang lint` exit codes and `Report.ok` as they
  were). Declared through the new `VerbSpec.no_band_on_temporal`, usable by contract
  authors.

### Changed (inline output)
- Timestamp literals in inline/display SQL now follow the dialect. Ten dialects changed:
  `trino` and bridged `presto`, `athena`, `dune` write
  `TIMESTAMP 'YYYY-MM-DD HH:MM:SS[.ffffff] UTC'` (an explicit zone, so the literal is the
  UTC instant whatever the session time zone); `databricks` and bridged `hive`, `spark`,
  `spark2`, `teradata`, `exasol` write `TIMESTAMP 'YYYY-MM-DD HH:MM:SS[.ffffff]'` (naive,
  holding UTC). Bridged `teradata` inlines booleans as `1`/`0`. Date literals, the
  quote-doubling decision (always the dialect's lexer) and bound-parameter SQL are
  unchanged.
- Precision caps: inlining refuses with `FL507` a `Decimal` whose plain-notation precision
  exceeds 38 digits (`Decimal('1E+50')` counts 51) and an `int` outside the signed 64-bit
  range, where engines silently change the literal's type (DuckDB reads them as `DOUBLE`,
  `HUGEINT` or `BIGNUM`). Evidence shows the `{{name}}` template instead; checks are
  unaffected.

### Fixed
- Guards on host-supplied values (`CachedCatalog` answers, `UnionAll` branches built by a
  custom lowering, non-finite numbers in caller-built parameters) no longer vanish under
  `python -O`: they raise `AssertionError` explicitly, as before, now with a message. Under
  `-O` a protocol-breaking catalog could turn an `error` into a `pass`.
- A custom contract whose plan returns `None` (or anything that is not a core or registered
  primitive) is refused by `plan_for` with a `ValueError` naming the verb. `check()` now
  returns the documented "plan failed" `error` outcome for that claim, identically under
  `-O`, and `compile()` returns the same outcome. Before, the claim was taken for a catalog
  verb: `check()` raised a bare `AssertionError` out of the call (under `-O` it reported a
  "catalog lookup failed" error instead), and `compile()` silently rendered a metadata
  query.
- Published verb examples (`verb_reference`, `factlang verbs`, the verbs reference page)
  no longer include valid examples that expect a diagnostic, such as the new `FL314`
  examples, which are `error` when judged.
- Caret lines in rendered diagnostics copy the tabs of the source line, so carets line up
  under tab-indented claims in any tab width.

### Documentation
- The grammar reference documents the continuation rule (blanks after `\` are allowed) and
  that a column named `TRUE` must be quoted while a lowercase `true` is a valid bare name;
  the agents guide recommends a read-only database role and explains that `--max-bytes`
  bounds cost only on dry-run-capable connectors while `--max-claims` bounds work; the
  verbs guide states 19 verbs (doc-tested); the connectors guide explains that
  non-weak-referenceable connections (sqlite3, pyodbc) are held for the process lifetime.

### Tests & CI
- The release workflow's verify job requires the tag push's own CI run with every
  `test-full` matrix leg green (no other run may substitute; 100-minute wait, 120-minute job
  timeout, transient `gh` failures retried), via `scripts/verify_tag_ci.py`.
- Engine timeout and DB-API thread tests use events and barriers instead of wall-clock
  budgets; the architecture test reports each violation once; the packs test helper no
  longer edits `sys.path`; the analyser test no longer leaks registered value types.
- New: a hash-preserving format property, hypothesis properties for the quoting helpers
  against the real parser, a bound-SQL snapshot pinning `sql_template` + parameters to 0.1.2
  for 19 dialects, sqlglot round-trips of every new spelling, the DuckDB verb matrix run
  normally and under `python -O` with identical outcomes, and every `assert` left in `src/`
  pinned as a reviewed internal invariant.

## [0.1.2] - 2026-11-25

Security: literal inlining trusts no value's own methods (post-0.1.1 security review).

### Security
- `inline()` accepts only values of an exact built-in type (`bool`, `int`, `float`,
  `Decimal`, `str`, `date`, `datetime`, or `None`) and refuses every subclass (`IntEnum`,
  `int`-`Enum` mixins, numpy scalars, `str`/`float`/`Decimal`/`date` subclasses), whose own
  `__repr__`/`__format__`/`isoformat` could print SQL. Values are rendered through the base
  type's functions or from their fields; timestamps are converted to UTC and accept only
  `datetime.timezone`/`zoneinfo` time zones.
- New diagnostic `FL507` *literal cannot be inlined safely* for every value-dependent
  refusal: non-scalars, non-finite numbers, subclasses, backslash/NUL, lone surrogates,
  integers or decimals with more than 4300 digits (`Decimal("1E+100000000")` used to render
  100M digits, a huge `int` raised `ValueError`), a value whose type does not match its
  family (a `datetime` in a `date` parameter, `True` in an `integer` one), and a `'` where
  the dialect has no doubled-quote form. These were `FL500`, which now means a genuine
  render bug only. `display_sql` falls back to the template on `FL507` and on any other
  exception from the conversion, but lets `FL500` propagate.
- The quote form follows the dialect the renderer resolves, not a fixed name list: a native
  profile's new `doubled_quote_escape` (`False` for `bigquery`, `databricks`;
  conformance-checked against sqlglot's tokenizer), or sqlglot's tokenizer for a bridged
  dialect. `spark`, `spark2` and `hive` (and names like `"bigquery, k=v"`) used to inline
  `''`, which their lexers read as the end of the literal; a dialect nothing knows refuses
  a `'`.
- T-SQL strings inline as national literals `N'…'` (no code-page conversion that could
  turn a look-alike character into `'`); T-SQL and Oracle booleans inline as `1`/`0`
  (`TRUE` is an identifier there); timestamps inline as `CAST('YYYY-MM-DD HH:MM:SS[.ffffff]'
  AS DATETIME2)` on T-SQL and `TIMESTAMP 'YYYY-MM-DD HH:MM:SS[.ffffff]'` on Oracle, and T-SQL
  dates as `CAST('YYYY-MM-DD' AS DATE)`. New `DialectProfile` fields
  `string_literal_prefix`, `boolean_literals`, `date_literal`, `timestamp_literal`; bridged
  dialects take the spellings of the nearest native ancestor (Fabric -> T-SQL).
- A custom profile cannot switch on `''` for a lexer that lacks it: when a native profile's
  name is also a dialect sqlglot knows, doubling is used only if both the profile and that
  dialect's tokenizer allow it (a postgres-like profile registered as `hive` refuses `'`
  with `FL507`). `DialectProfile.doubled_quote_escape` now defaults to `False` (fail
  closed); every built-in profile sets it explicitly.
- A string holding a raw line terminator (CR, LF, U+0085, U+2028, U+2029) is refused with
  `FL507` in every dialect, so `"x\n\nDROP TABLE t;"` can never reach a console such as
  SQL*Plus, which ends the SQL at a blank line; evidence shows the template instead.

### Changed
- `RenderedProbe.inline()` / `RenderedFragment.inline()` take an optional `style`
  (`factlang.sql.probe.LiteralStyle`) and default to the probe's own dialect
  (`factlang.sql.registry.literal_style_for`).

### Documentation
- The evidence guide states what inline text assumes: display or hosts without binding, a
  UTF-8 (or single-byte-safe) client charset, and console tools (SQL*Plus `&`, SSMS `GO`,
  driver paramstyle parsing of `%s`/`?`) that may reinterpret pasted text, including
  SQL*Plus ending a statement at a blank line.
- Timestamps inline as naive-UTC literals of the dialect's timestamp type, so a comparison
  with a time-zone-aware column depends on the session time zone (bound parameters behave
  the same); wording that claimed independence from all server settings now says
  string-escape settings.

## [0.1.1] - 2026-11-24

Security hardening: fail-closed literal inlining.

### Security
- `inline()` on probes and fragments is display-oriented, but hosts may execute it when a
  driver cannot bind, so it now fails closed. Values it cannot render safely raise
  `RenderError("FL500 cannot inline <type> parameter <name> …")`: non-scalars (lists,
  dicts, sets, bytes, arbitrary objects, which used to become a quoted `str(v)`), `NaN` and
  infinities (which used to be inlined bare), and strings holding a backslash or NUL.
- String literals no longer depend on server settings: every dialect quotes by doubling
  `'`, and the backslash-escaping used for `bigquery`, `mysql`, `databricks` and
  `clickhouse` is gone. `bigquery` and `databricks` have no doubled-quote form, so a `'`
  in a string is refused there.

### Changed
- `Evidence.sql`, `Checker.estimate`, `factlang explain` and the agent's `factlang_explain`
  show the `{{name}}` template (values in `params`) when a literal cannot be inlined; a
  check never fails because its evidence could not be inlined. New helper
  `factlang.engine.display_sql`.
- Decimal literals inline in plain positional form (`1000`, not `1E+3`).

## [0.1.0] - 2026-11-24

The first release. Development status: Alpha. Python 3.10–3.13.

### Added
- **The language, version 1.0** (`fl.LANGUAGE_VERSION`): line-oriented claims with typed
  literals, `WHERE` filters (including bare boolean atoms), `USE` directives, canonical
  formatting and a semantic hash; stable `FLnnn` diagnostics with carets and did-you-mean
  suggestions. The grammar reference is normative and executed by the test suite.
- **19 standard verbs** in the `standard` pack 1.0: `EXISTS`, `TYPE`, `ROWS`, `COUNT`,
  `NULLS`, `DEAD`, `UNIQUE`, `FANOUT`, `VALUES`, `RANGE`, `PATTERN`, `FRESH`, `JOIN`,
  `DISTINCT`, `SUM`, `AVG`, `MIN`, `MAX`, `NOTE`. Contracts and packs are extensible
  (register, override, alias, remove, extend, merge, the decorator API, `fl.check_pack`,
  entry-point discovery with `fl.discover_packs`).
- **11 native dialects** rendered by the stdlib-only core: `duckdb`, `postgres`, `mysql`,
  `clickhouse`, `tsql`, `trino` (tier `verified`, executed in CI) and `bigquery`,
  `snowflake`, `redshift`, `databricks`, `oracle` (tier `native`); any other dialect
  sqlglot can write through the optional sqlglot bridge (tier `best_effort`).
- **Connectors**: `DBAPIConnector` (any PEP 249 connection), `CallableConnector`,
  `SQLAlchemyConnector`, `BigQueryConnector`, `fl.connect`; catalogs (`StaticCatalog`,
  `NullCatalog`, `CachedCatalog`, `WarehouseCatalog`) and the `Catalog` protocol.
- **Engine**: `Checker.check` / `acheck` with probe fusion and batching; every outcome
  carries facts and evidence (SQL, typed bind parameters, dialect, tier, timings);
  `Report.verified` (every measurable claim was measured and passed, stricter than `ok`).
  `Checker.measure` / `ameasure` and `Checker.estimate` (cost without executing).
- **Command line** `factlang`: `lint`, `fmt`, `explain`, `check` (`--dsn`, `--connector`,
  `FACTLANG_DSN`), `verbs`, `mcp`; text, JSON and Markdown output; exit codes 0/1/2/3.
- **Agent tools**: `factlang_check`, `factlang_lint`, `factlang_explain` tool definitions,
  a dispatcher and verb references for prompts; the same tools served over MCP on stdio
  (`factlang mcp`, extra `mcp`).
- **Amendment A** (requirements of a dependent schema profiler): raw measurement of IR
  primitives without a claim (`fl.measure`, `Measurement`); exact top-k observed values
  (`VALUES … IN []`); composite join keys (`JOIN … WITH [..] TO [..]`); reverse and row
  join facts (`coverage`, `row_match_rate`, `fan_in`, …, `COVERAGE`); cost estimates
  (`CostEstimate`); public `fl.parse_filter`, `fl.sql.render_filter`, `fl.sql.render_join`,
  `RenderedFragment`; the native `trino` profile; guarded metadata SQL
  (`fl.connectors.run_metadata`, `FL703`); bare boolean filter atoms.
- **Amendment B**: sampled raw measurement for very large tables. `SampleSpec`
  (`keyhash`, `system`, `bernoulli`; deterministic seeds) on IR primitives, rendered per
  dialect, used **only** by `Checker.measure`. No verdict is ever produced from sampled
  data: a sampled plan in a claim is `FL404`, `FL405` and `FL506` guard the methods.
- **Stable public API** (R10): `fl.__all__` and the table in the
  [API reference](https://rupakc.github.io/factlang/reference/api/) follow SemVer, with no
  breaking change within a `0.x` minor series. Includes `fl.Ident`, `fl.TableRef`,
  `fl.ColumnRef`, `fl.EdgeRef`, `fl.check_pack`, `fl.discover_packs`, `fl.Catalog`,
  `fl.types.LiteralKind` (also `from factlang.types import LiteralKind`), `fl.sql.Param`,
  `factlang.syntax.FILTER_KEYWORDS`.
- `factlang mcp` reads `FACTLANG_DSN` when neither `--dsn` nor `--dialect` is given.
- Documentation site with an in-browser playground, GitHub wiki, CI on Python 3.10–3.13
  with service-container integration tests, and a tag-triggered release workflow
  (PyPI Trusted Publishing and a GitHub Release with the sdist and wheel).

### Fixed
- A quoted and a bare spelling of one table in the same document no longer fuse into one
  probe (both claims were reported as `FL500`).
- `str(Verdict.PASS)` is `"pass"`.

[Unreleased]: https://github.com/rupakc/factlang/compare/v0.1.4...HEAD
[0.1.4]: https://github.com/rupakc/factlang/compare/v0.1.3...v0.1.4
[0.1.3]: https://github.com/rupakc/factlang/compare/v0.1.2...v0.1.3
[0.1.2]: https://github.com/rupakc/factlang/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/rupakc/factlang/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/rupakc/factlang/releases/tag/v0.1.0
