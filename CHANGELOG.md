# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The language has its own version
(`LANGUAGE_VERSION`), independent of the package version.

## [Unreleased]

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

[Unreleased]: https://github.com/rupakc/factlang/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/rupakc/factlang/releases/tag/v0.1.0
