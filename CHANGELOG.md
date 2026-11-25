# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The language has its own version
(`LANGUAGE_VERSION`), independent of the package version.

## [Unreleased]

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

[Unreleased]: https://github.com/rupakc/factlang/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/rupakc/factlang/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/rupakc/factlang/releases/tag/v0.1.0
