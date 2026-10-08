# BioKG-Align kit — documentation

Reference material that complements the kit's `README.md` (kit 0.4.0, release v0.4.0). Each
document is self-contained; cross-references are explicit. The machine-readable contract is
[`submission_schema.json`](../submission_schema.json).

## Contents

| File | What you'll find |
|------|------------------|
| [submission_format.md](submission_format.md) | The five-column, ID-joined submission TSV: columns, types, header. |
| [submission_scoring.md](submission_scoring.md) | The join by `QueryID`, the eight rules (strict on the platform, lenient for local train/valid scoring), worked example. |
| [metric_families.md](metric_families.md) | Preferred-pair, Hierarchy-Aware Typed nDCG@10, diagnostic families; key names; macro and repeat-run conventions. |
| [graded_relevance.md](graded_relevance.md) | Gain ladder (with the equivalence partial gain parameter) and the construction of `graded.tsv`. |
| [pool_model.md](pool_model.md) | Queries per source, why their pools differ, opaque IDs and shuffled rows, `QueryID` keying. |
| [building_with_kit.md](building_with_kit.md) | End-to-end workflows for the typical participant journey. |
| [complex_track.md](complex_track.md) | High-level pointer (the complex track is out of kit scope). |
| [data_card.md](data_card.md) | Release file inventory and column schema for every released artefact. |

## Conventions used throughout

- **Source vs target.** A "source" entity is the query entity; a "target" entity is a
  candidate from the partner ontology.
- **Relation shorthand.** `eq` ≡ `equivalent`, `ssbt` ≡ `source_subsumed_by_target`, `sst` ≡
  `source_subsumes_target`. The shorthands never appear in any file or submission row.
- **Per-query keying.** Every per-query lookup is keyed by the opaque `QueryID`
  (`<task>-<8 hex>`). A source can own two queries with different pools; never key by
  `SrcEntity`.
