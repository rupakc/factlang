# Glossary

> Plain definitions of the words used across this wiki, in alphabetical order.
> Each entry points to the page that explains it in depth.
> If a term is not here, check [[Concepts]].

- **Alias** (metric): the fixed name of a result column such as `row_count`. A wire format. See [[IR]].
- **Alias** (verb): a second name for a verb in a pack. See [[Extending-Packs]].
- **Analyzer**: checks a parsed claim against verbs and names. See [[Analyzer]].
- **Arity**: how many rows a primitive returns (one row or a set of rows). See [[IR]].
- **Bind parameter**: a value sent separately from the SQL text; every literal is one. See [[Dialects]].
- **Catalog**: answers whether a table or column exists. See [[Catalogs]].
- **Claim**: one line stating one fact. See [[Language-Guide]].
- **Connector**: runs SQL and returns rows. See [[Connectors]].
- **Contract**: one object describing a verb completely. See [[Extending-Verbs]].
- **Decide**: the predicate step that turns facts into pass or fail. See [[Judge]].
- **Derive**: the step that computes facts from raw rows, returning `None` when undecidable. See [[Judge]].
- **Dialect profile**: the small record of decisions that prints SQL for one warehouse. See [[Dialects]].
- **Diagnostic**: a coded message with a span. See [[Diagnostics]].
- **Evidence**: the SQL, parameters, tier and timing behind a verdict. See [[Concepts]].
- **Fact**: a named number computed from a measurement. See [[Judge]].
- **Filter**: the trailing `WHERE` condition. See [[Language-Guide]].
- **Fusion**: merging several measurements into one query. See [[Optimizer]].
- **Fragment**: a rendered filter or join snippet, not a full statement. See [[Measurement-API]].
- **IR**: the dialect-free list of measurement primitives. See [[IR]].
- **Keyhash**: deterministic sampling by hashing a key, so samples repeat and joins align. See [[Measurement-API]].
- **Layer**: a level in the import hierarchy; modules import only lower layers. See [[Architecture]].
- **Measurement**: the raw result of running a primitive, with no verdict. See [[Measurement-API]].
- **Operand**: a table, column or edge a claim is about. See [[Language-Guide]].
- **Outcome**: a verdict plus facts, explanation and evidence. See [[Judge]].
- **Pack**: a named, versioned set of verb contracts. See [[Extending-Packs]].
- **Predicate**: a small comparison over facts. See [[Judge]].
- **Primitive**: one kind of measurement in the IR. See [[IR]].
- **Probe**: the rendered SQL plus parameters for a plan. See [[Dialects]].
- **Semantic hash**: an identity that ignores spelling differences. See [[Parser]].
- **Tier**: `verified`, `native` or `best_effort`. See [[Concepts]].
- **Unprobeable**: a claim that cannot be tested by SQL by design. See [[Standard-Verbs]].
- **Verb**: the first word of a claim, naming the kind of fact. See [[Standard-Verbs]].
- **Verdict**: pass, fail, no_data, error, unprobeable or invalid. See [[Concepts]].

## See also

- [[Concepts]]
- [[FAQ]]
- [[Home]]
