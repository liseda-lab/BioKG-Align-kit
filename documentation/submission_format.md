# Submission format

A BioKG-Align submission is a single TSV file covering all three task pairs in the canonical order. This document defines the per-row structure; [block_scoring.md](block_scoring.md) covers the per-query grouping and validation rules.

## File layout

- **Separator:** tab character (`\t`). Submissions must not use commas, spaces, or any other separator.
- **Header row:** required. The header is exactly `SrcEntity\tTgtEntity\tRelation\tScore` and must appear as the first line of the file.
- **Encoding:** UTF-8. Identifiers are ASCII-only in the released data (`[A-Za-z0-9_:-]`), so UTF-8 vs ASCII is observably equivalent on well-formed submissions.
- **Line endings:** LF (`\n`). The scorer tolerates CRLF input but produces LF for any file it writes.

## Columns

| Column     | Type   | Description                                                     |
|------------|--------|-----------------------------------------------------------------|
| `SrcEntity`| string | Source entity identifier (e.g. `NCIT:C2991`). Must match the canonical `SrcEntity` for this query's block. |
| `TgtEntity`| string | Target entity identifier; must be a member of this query's candidate set. |
| `Relation` | string | One of `equivalent`, `source_subsumed_by_target`, `source_subsumes_target`. |
| `Score`    | float  | Real-valued confidence. Higher = more confident. Any finite float is valid; NaN and infinity are rejected. |

## Score semantics

Scores are interpreted as a strict order over $(\mathrm{target}, \mathrm{relation})$ pairs within a query block. There is no required range ($[0, 1]$, $[-\infty, \infty]$, anything calibrated, anything uncalibrated) — only the relative order matters.

Ties are broken deterministically by:

1. ascending `TgtEntity`,
2. then by relation in the fixed order $\equiv\ \prec\ \sqsubseteq\ \prec\ \sqsupseteq$ (`equivalent`, then `source_subsumed_by_target`, then `source_subsumes_target`).

If you re-rank rows in any post-processing pipeline (sort, group, deduplicate, ...) preserve the convention, otherwise your local kit scores will diverge from the leaderboard.

## What is **not** in a submission row

The submission row has no `QueryID` column. The public `test.cands.tsv` also has no `QueryID` column — both are reconstructed positionally by the scorer from the row order of `test.cands.tsv`. See [block_scoring.md](block_scoring.md) for how positional recovery works in detail; see [pool_model.md](pool_model.md) for why some source entities appear in two adjacent blocks under the pool model.

## Worked example

```text
SrcEntity   TgtEntity   Relation                     Score
NCIT:C2991  DOID:1909   equivalent                   0.987651
NCIT:C2991  DOID:1909   source_subsumed_by_target    0.123456
NCIT:C2991  DOID:1909   source_subsumes_target       0.012345
NCIT:C2991  DOID:9970   equivalent                   0.456789
...
```

(One block of 150 rows for one query begins here; the next block of 150 rows would start once all $50 \text{ candidates} \times 3 \text{ relations}$ rows for this query are written.)
