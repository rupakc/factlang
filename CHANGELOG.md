# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/). The language has its own version
(`LANGUAGE_VERSION`), independent of the package version.

## [Unreleased]

### Added
- Repository skeleton, CI, architecture tests and agent scaffolding.
- `Report.verified`: every measurable claim was measured and passed (stricter than `ok`);
  first key of `Report.to_dict()` and of the `factlang_check` tool result.
- Stable public paths: `fl.Ident`, `fl.TableRef`, `fl.ColumnRef`, `fl.EdgeRef`,
  `fl.check_pack`, `fl.Catalog`, `fl.types.LiteralKind`, `fl.sql.Param`,
  `factlang.syntax.FILTER_KEYWORDS`.
- `factlang mcp` reads `FACTLANG_DSN` when neither `--dsn` nor `--dialect` is given.

### Fixed
- A quoted and a bare spelling of one table in the same document no longer fuse into one
  probe (both claims were reported as `FL500`).
- `str(Verdict.PASS)` is `"pass"`.
