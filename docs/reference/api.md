# Python API

```python
import factlang as fl
```

Everything in `fl.__all__` is public API, and so is the `__all__` of each public package:
`fl.ir`, `fl.sql`, `fl.connectors`, `fl.types`, `fl.syntax` (`factlang.syntax`) and
`factlang.agent`. Import from those paths; any other module path (`factlang.semantics.…`,
`factlang.ir.primitives`, `factlang.connectors.base`, …) is internal and may move. The
package follows SemVer from `0.1.0`; the language itself is versioned separately
(`fl.LANGUAGE_VERSION == "1.0"`).

## Stable API

The following are public API **under SemVer: no breaking change within a `0.x` minor
series**. Libraries built on FactLang, schema profilers in particular, may depend on them.

| item | |
|---|---|
| `factlang.ir` primitives (`ColumnAggregate`, `CompositeKeyAggregate`, `ValueMembership`, `KeyOverlap`, `PatternCoverage`, `MetadataQuery`), their fields and result columns, `Metric`, `SampleSpec` | [IR primitives](../extending/primitives.md) |
| `Checker.measure`, `Checker.ameasure`, `Checker.estimate`, `fl.measure`, `Measurement`, `CostEstimate` | [Raw measurement](../guide/measure.md) |
| `fl.parse_filter`, `fl.sql.render_filter`, `fl.sql.render_join`, `RenderedFragment` | [Fragments](../extending/fragments.md) |
| `fl.connectors.run_metadata` (also `fl.run_metadata`) | [Metadata SQL](../guide/measure.md#metadata-sql) |
| `fl.Ident`, `fl.TableRef`, `fl.ColumnRef`, `fl.EdgeRef` (also in `fl.ir`) | [IR primitives](../extending/primitives.md) |
| `fl.check_pack`, `fl.Catalog` (the catalog protocol) | [Packs](../extending/packs.md), [Catalogs](../extending/catalogs.md) |
| `fl.types.LiteralKind`, `fl.types.ValueType`, `fl.types.register_type` | [Value types](../extending/types.md) |
| `fl.sql.Param` (a typed bind parameter) | [Fragments](../extending/fragments.md) |
| `fl.connectors.Connector`, `AsyncConnector`, `QueryResult`, `ExecutionError` | [Connectors](../extending/connectors.md) |
| `factlang.syntax.FILTER_KEYWORDS`: the words reserved inside a `WHERE` filter (the filter-keyword set only, not every keyword of the language) | [Filters](../guide/filters.md) |
| `Report.verified` and `Report.ok` | [Verdicts](../guide/verdicts.md#reports) |

The metric aliases (`row_count non_null distinct_keys max_rows_per_key min_value max_value
sum_value avg_value duplicate_keys server_now`) are a wire format and do not change at all.

## One-call helpers

::: factlang.api.lint

::: factlang.api.format

::: factlang.api.compile

::: factlang.api.check

::: factlang.api.measure

::: factlang.api.parse_filter

::: factlang.syntax.parser.parse

`fl.check` with a single claim returns an `Outcome`; with any other number it returns a
`Report`. To get document-level diagnostics (a broken `USE` line, for instance) always use
`Checker.check`, which returns a `Report`.

`Report.verified` is the flag to gate on: every outcome that is not `UNPROBEABLE` is `PASS`,
there is at least one such outcome, and no error diagnostic was raised. `Report.ok` is
weaker: it only rules out `FAIL`, `ERROR` and `INVALID`, so a report with `NO_DATA`
outcomes, or with nothing measured at all, is ok but not verified.

## Checking

::: factlang.engine.Checker
    options:
      members: [check, acheck, compile, measure, ameasure, estimate, offline_catalog]

::: factlang.engine.Report

::: factlang.judge.Outcome

::: factlang.judge.Verdict

::: factlang.judge.outcome.Evidence

::: factlang.engine.Measurement

::: factlang.engine.CostEstimate

## Contracts and packs

::: factlang.packs.standard.standard_pack

::: factlang.contract.ContractPack
    options:
      members: [verb, register, override, alias, remove, extend, merge, get, names, contracts]

::: factlang.contract.Contract

::: factlang.contract.Example

::: factlang.contract.check_pack

::: factlang.semantics.signature.Operand

::: factlang.semantics.signature.AttrSpec

## Predicates

::: factlang.judge.predicate.Compare

::: factlang.judge.predicate.CompareUsingClaimOp

::: factlang.judge.predicate.Within

::: factlang.judge.predicate.And

::: factlang.judge.predicate.Or

::: factlang.judge.predicate.Not

::: factlang.judge.predicate.WhenClaimHas

## IR

::: factlang.ir.primitives.ColumnAggregate

::: factlang.ir.primitives.CompositeKeyAggregate

::: factlang.ir.primitives.ValueMembership

::: factlang.ir.primitives.KeyOverlap

::: factlang.ir.primitives.PatternCoverage

::: factlang.ir.primitives.MetadataQuery

::: factlang.ir.primitives.SampleSpec

::: factlang.ir.metrics.Metric

::: factlang.ir.primitives.register_primitive

## SQL

::: factlang.sql.registry.render_filter

::: factlang.sql.registry.render_join

::: factlang.sql.fragment.RenderedFragment

::: factlang.sql.probe.RenderedProbe

::: factlang.sql.registry.register_profile

::: factlang.sql.registry.native_dialects

## Connectors

::: factlang.connectors.base.Connector

::: factlang.connectors.base.QueryResult

::: factlang.connectors.base.ExecutionError

::: factlang.connectors.base.run_metadata

::: factlang.connectors.dbapi.DBAPIConnector

::: factlang.connectors.callable.CallableConnector

::: factlang.connectors.sqlalchemy.SQLAlchemyConnector

::: factlang.connectors.sqlalchemy.connect

::: factlang.connectors.bigquery.BigQueryConnector

## Catalogs

::: factlang.semantics.catalog.Catalog

::: factlang.semantics.catalog.NullCatalog

::: factlang.semantics.catalog.StaticCatalog

::: factlang.semantics.catalog.CachedCatalog

::: factlang.connectors.catalog.WarehouseCatalog

## Value types

::: factlang.semantics.types.ValueType

::: factlang.semantics.types.register_type

## Agent surface

::: factlang.agent.tools.tool_definitions

::: factlang.agent.tools.handle_tool_call

::: factlang.agent.reference.verb_reference
