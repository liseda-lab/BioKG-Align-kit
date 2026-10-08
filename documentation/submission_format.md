# Submission format

A BioKG-Align submission (kit 0.4.0, format id `biokg-align-submission-v0.4.0-ids`) is a
single TSV file covering **every task of the phase**. Rows are joined to queries by their
`QueryID`; the row order is irrelevant. This document defines the per-row structure;
[submission_scoring.md](submission_scoring.md) covers the join, the validation rules and the
lenient local mode.

## File layout

- **Separator:** tab (`\t`).
- **Header row:** required, exactly `QueryID\tSrcEntity\tTgtEntity\tRelation\tScore`.
- **Encoding:** UTF-8. Identifiers in the released data are ASCII.
- **Line endings:** LF (`\n`).
- **One file for all tasks.** The task of a row is the prefix of its `QueryID`.

## Columns

| Column      | Type   | Description |
|-------------|--------|-------------|
| `QueryID`   | string | The query's opaque identifier, copied from `tasks/<task>/<split>.cands.tsv` (pattern `^[A-Z]+-[A-Z]+-[0-9a-f]{8}$`, e.g. `SNOMED-FMA-3fa94c0e`). |
| `SrcEntity` | string | The query's source entity, exactly as in the candidate file. |
| `TgtEntity` | string | A member of the query's candidate set. |
| `Relation`  | string | One of `equivalent`, `source_subsumed_by_target`, `source_subsumes_target`. |
| `Score`     | float  | Higher = more confident. Any finite float (negative values included); NaN and infinity are rejected. |

Every query must carry **exactly one row per (candidate, relation) pair**: 50 candidates × 3
relations = 150 rows per query in the canonical release.

## Query identity

`QueryID` is opaque: the eight hexadecimal digits are a keyed hash and carry no
information about the query (in particular not its mode). A source entity may own two
queries — an equivalence-mode and a subsumption-mode query — with different `QueryID`s and
different candidate pools (see [pool_model.md](pool_model.md)). Always key your own
bookkeeping by `QueryID`, never by `SrcEntity`.

## Score semantics

Only the order of scores within a query matters. Ties are broken deterministically by

1. ascending `TgtEntity`, then
2. relation order `equivalent` ≺ `source_subsumed_by_target` ≺ `source_subsumes_target`.

## Worked example

```text
QueryID              SrcEntity    TgtEntity   Relation                    Score
NCIT-DOID-5f0c2a91   NCIT:C2991   DOID:1909   equivalent                  0.987651
NCIT-DOID-5f0c2a91   NCIT:C2991   DOID:1909   source_subsumed_by_target   0.123456
NCIT-DOID-5f0c2a91   NCIT:C2991   DOID:1909   source_subsumes_target      0.012345
SNOMED-FMA-0b7d33e4  SNOMED:8089  FMA:7088    equivalent                  -1.5
...
```

`evaluation/sample_submission.tsv` in the release shows the header with two rows of an
obviously fake query (`EXAMPLE-TASK-00000000`). `evaluation/submission_schema.json` (a copy of
the kit's [`submission_schema.json`](../submission_schema.json)) states the contract in
machine-readable form.

## Changes from the v0.2–v0.3 format

The positional four-column block format (`SrcEntity, TgtEntity, Relation, Score`, rows grouped
by position into 150-row blocks) is **removed**: a file in that format is rejected with a
header error. The test candidate files now carry `QueryID`; the train/valid candidate files no
longer carry gold columns (the gold moved to `evaluation/`).
