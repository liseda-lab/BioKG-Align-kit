# BioKG-Align kit — documentation

This directory holds reference material that complements the kit's `README.md`. Each document is self-contained; cross-references between them are explicit. Nothing here is normative beyond what the proposal, the `data_card.md`, and the `submission_schema.json` already establish — these documents exist so you don't have to re-derive the contract from source code.

## Contents

| File | What you'll find |
|------|------------------|
| [submission_format.md](submission_format.md) | The four-column submission TSV: columns, types, encoding, header. |
| [block_scoring.md](block_scoring.md) | Per-query block grouping, strictness rules, worked examples. |
| [metric_families.md](metric_families.md) | Preferred-pair, Hierarchy-Aware Typed nDCG@10, diagnostic — definitions and worked numbers. |
| [graded_relevance.md](graded_relevance.md) | Gain ladder and the construction rule for `*.graded.tsv`. |
| [pool_model.md](pool_model.md) | Per-source Q0 + Q1 queries and the per-`(SrcEntity, QueryID)` keying contract. |
| [building_with_kit.md](building_with_kit.md) | End-to-end workflows for the typical participant journey. |
| [complex_track.md](complex_track.md) | High-level pointer (the complex track is out of kit scope). |
| [data_card.md](data_card.md) | Public-release file inventory and column schema for every released artefact. |

[submission_schema.json](../submission_schema.json) gives the submission contract in machine-readable form.

## Conventions used throughout

- **Source vs target.** A "source" entity is the query entity (the one whose alignment is being looked up); a "target" entity is a candidate from the partner ontology.
- **Relation shorthand.** Throughout the documents, `eq` $\equiv$ `equivalent`, `ssbt` $\equiv$ `source_subsumed_by_target`, `sst` $\equiv$ `source_subsumes_target`. These shorthands never appear in any on-disk file or submission row.
- **Per-query keying.** Every per-query lookup is keyed by `(SrcEntity, QueryID)`. Under the pool model the same `SrcEntity` contributes both a Q0 and a Q1 query; collapsing by source alone loses one of them.
