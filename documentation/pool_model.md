# The pool model

Under the canonical build, each source entity contributes two queries, not one.
(Sources with several reference mappings and no equivalence contribute one
single-gold query per mapping — `Q0..Qn` — with the other mappings' targets
excluded from each query's pool, so every ⟨query, candidate-set⟩ pair contains
exactly one correct answer.) This document explains what the pool model looks like in the released files and why the kit keys every per-query lookup by $(\mathrm{SrcEntity}, \mathrm{QueryID})$.

## What the pool model means

For each source entity in a task pair, the build emits two queries:

- **Q0** — equivalence query. The gold pair is the equivalence target in the partner ontology, with relation `equivalent`.
- **Q1** — subsumption query. The gold pair is one of the source entity's ancestors or descendants in the partner ontology, with relation `source_subsumed_by_target` or `source_subsumes_target`.

The two queries share the same `SrcEntity` but have distinct `QueryID` values (`Q0` and `Q1`), distinct preferred pairs, and generally distinct candidate sets (the build is allowed to re-sample candidates per query; in the canonical release the two queries from a given source share the same 50-candidate pool, but the contract does not require this).

Under the canonical build at fraction=1.0:

```text
N_sources (test) = 15,168
N_queries (test) = 29,490   # 15,168 Q0 (equivalence) + 14,322 Q1 (subsumption-only)
```

The `tasks/<task>/test.cands.tsv` file therefore has 29,490 rows across the three task pairs (the same `SrcEntity` appears on consecutive rows for the sources that contribute both queries; some sources yield only Q0, so the 14,322 Q1 queries are slightly fewer than the 15,168 sources), and the corresponding submission has $29{,}490 \times 150 = 4{,}423{,}500$ rows.

## What this looks like on disk

The public `tasks/<task>/{train,valid}.cands.tsv` files carry a `QueryID` column explicitly, with the single primary gold in `TgtEntities`/`Relations` (one-element lists under the N=1 gold model):

```text
SrcEntity   QueryID  TgtEntities    Relations                       TgtCandidates
NCIT:C001   Q0       ['DOID:D001']  ['equivalent']                  [...50 candidates...]
NCIT:C001   Q1       ['DOID:D000']  ['source_subsumed_by_target']   [...50 candidates...]
NCIT:C002   Q0       ['DOID:D002']  ['equivalent']                  [...50 candidates...]
NCIT:C002   Q1       ['DOID:D000']  ['source_subsumed_by_target']   [...50 candidates...]
```

The `tasks/<task>/test.cands.tsv` file is two-column (no `QueryID`, no gold) — the participant has no information about which queries are Q0 vs Q1 for the test split. Candidate lists are in canonical (sorted) order: the ordering carries no information about the gold. The same source appears twice on adjacent rows; the scorer recovers the `QueryID` positionally from the private organiser-side answers file.

The `tasks/<task>/{train,valid}.preferred.tsv` file likewise carries `QueryID`:

```text
SrcEntity   QueryID  TgtEntity   Relation
NCIT:C001   Q0       DOID:D001   equivalent
NCIT:C001   Q1       DOID:D000   source_subsumed_by_target
NCIT:C002   Q0       DOID:D002   equivalent
NCIT:C002   Q1       DOID:D000   source_subsumed_by_target
```

Same for the graded relevance files.

## Per-`(SrcEntity, QueryID)` keying contract

Every per-query lookup in the kit is keyed by the $(\mathrm{SrcEntity}, \mathrm{QueryID})$ tuple. Concretely:

- `scoring.load_answers(path)` returns `dict[(SrcEntity, QueryID), set[(TgtEntity, Relation)]]`.
- `scoring.load_preferred_pairs(path)` returns `dict[(SrcEntity, QueryID), (TgtEntity, Relation)]`.
- `hierarchy.load_graded_relevance(path)` returns `dict[(SrcEntity, QueryID), dict[(TgtEntity, Relation), gain]]`.
- `scoring.score_prediction_rows(...)` partitions predictions by $(\mathrm{SrcEntity}, \mathrm{QueryID})$ before scoring.

A kit that keyed by `SrcEntity` alone — as pre-v0.1.3 versions did — silently collapsed the Q0 and Q1 rows for a shared source. Under that bug:

- `load_preferred_pairs` keeps only one of the two preferred pairs per source (later row wins).
- `load_answers` merges Q0 + Q1 gold sets into one set per source, which both double-counts (set union) and loses the per-query cardinality every metric needs.
- `score_files` reports $N/2$ queries instead of $N$, and every metric is computed against the wrong query partitioning.

The paired-query regression tests in `tests/test_kit.py` pin all three load functions and the end-to-end score against the bundled `examples/mini_paired/` fixture, which has $2$ sources $\times$ $2$ queries exactly to exercise this contract.

## Predictions don't carry `QueryID`

The submission TSV has four columns; `QueryID` is **not** one of them. Under the pool model, the same `SrcEntity` appears in two adjacent submission blocks. The scorer assigns blocks to queries by position against the `test.cands.tsv` row order, not by matching `SrcEntity` to a query identifier.

The practical consequence: in your submission code, walk the public `test.cands.tsv` row by row and emit one block per row in lockstep with the cands file. Don't try to be clever and dedupe by `SrcEntity` — that silently corrupts the submission.

The kit's `verify` validator enforces positional alignment row-by-row and will reject a submission whose block-$k$ rows have a `SrcEntity` disagreeing with the cands file's row-$k$.
