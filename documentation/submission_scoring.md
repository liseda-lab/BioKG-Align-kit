# Submission scoring

How a submission is joined to the queries, which violations are fatal, what the kit's local
lenient mode does instead, and how the per-query scores are computed. The implementation is
`biokg_align_kit.scoring` — the **single scorer**: the platform scoring program and the
organiser's private test scoring call the same functions.

## The join

1. The **query index** is built from the candidate files of the phase
   (`tasks/<task>/<split>.cands.tsv`, columns `QueryID, SrcEntity, TgtCandidates`). Each file
   must sit in a directory named after its task; every `QueryID` must carry that task as its
   prefix; IDs are unique across the release; pools contain no empty lists and no duplicates.
2. Every submission row is attached to the query named by its `QueryID`. Row order never
   matters.
3. Each query's rows must cover the cartesian product *candidates × relations* exactly once.

## Rules and their two modes

`verify`, the platform scorer and the organiser's private test scoring are **strict**: every
rule is fatal and the error names the offending queries and lines (at most 50 examples per
rule). The kit's `score` command for **train/valid** is **lenient by default**: the same checks
run, every violation is reported as a warning with the fallback below, and scoring proceeds over
the queries that are present. `score --strict` applies the platform behaviour.

| # | Rule | Strict (platform, `verify`, `score --strict`) | Lenient (`score` on train/valid) |
|---|------|------|------|
| 1 | Header is exactly `QueryID, SrcEntity, TgtEntity, Relation, Score` (and every row has five fields) | fatal | header: fatal (nothing can be joined); malformed row: dropped |
| 2a | Every `QueryID` exists in the index | fatal | row dropped, count reported |
| 2b | Every indexed query appears | fatal | query skipped; `queries_expected` vs `queries_scored` reported |
| 3 | `SrcEntity` equals the query's source | fatal | row dropped |
| 4 | `TgtEntity` is in the query's candidate set | fatal | row dropped |
| 5 | `Relation` is one of the three relations | fatal | row dropped |
| 6 | `Score` parses to a finite float | fatal | row dropped |
| 7 | No duplicate `(QueryID, TgtEntity, Relation)` | fatal | maximum score kept |
| 8 | Every query has exactly `candidate_count × 3` rows | fatal | missing pairs of a present query filled with `-inf` (last rank), count reported |

Lenient semantics, precisely:

- a query is **present** iff it keeps at least one valid row after invalid rows are dropped; a
  query whose rows were all dropped is skipped, not ranked as all-`-inf`;
- the reference files are validated in full before scoring is restricted to present queries;
- a task with no scored query reports only `{queries_expected, queries_scored: 0,
  submission_warnings}`;
- the macro averages the rate metrics over the tasks with at least one scored query, and sums
  the count metrics over every requested task (scoring 10 of 10 queries of task A and 0 of 90
  of task B reports `queries_expected = 100`, `queries_scored = 10`, `tasks_scored = 1`);
- a submission in which no query has a valid row raises, even in lenient mode;
- lenient results carry `queries_expected`, `queries_scored` and `submission_warnings` (the
  number of violations attributed to the task); violations no scored task can own — malformed
  rows, and unknown `QueryID`s without a task prefix or of a task that is not being scored — are
  reported in the macro block as `submission_warnings_unattributed`, and
  `submission_warnings_total` adds them to the per-task sum; the CLI prints that the numbers are
  **not leaderboard-comparable** whenever a warning fired.

## The reference files are validated too

Before scoring a task, `score_task` loads `evaluation/<task>/<split>.answers.tsv`, its
siblings `<split>.preferred.tsv` and `<split>.graded.tsv`, and `evaluation/query_metadata.tsv`,
and runs the shared validator `biokg_align_kit.evaluation.validate_evaluation_set` — the same
function the organiser's release validation runs. It raises, naming the IDs, if any answers ID
lacks exactly one preferred pair that is in its gold set and its pool; if a graded row names an
off-pool target, an unknown relation, a gain outside (0, 1] or a duplicate pair; if the
preferred pair's gain is not 1.0; or if a query lacks its metadata row or the metadata
`QueryMode` contradicts the preferred relation.

## How one query is scored

1. Its 150 rows are ranked by descending `Score`, ties broken by `TgtEntity` and then the
   relation order `equivalent` ≺ `source_subsumed_by_target` ≺ `source_subsumes_target`.
2. The rank of the query's single preferred `(target, relation)` pair gives the reciprocal
   rank (primary metric: macro Preferred Typed MRR) and Hits@K.
3. The graded gains (see [graded_relevance.md](graded_relevance.md)) give Hierarchy-Aware Typed
   nDCG@10 (secondary metric).
4. If the top-ranked row's target is the preferred target, the query also contributes to the
   gated relation-typing diagnostic with the top row's relation.

Per-task values are arithmetic means over queries; the macro is the unweighted mean over tasks.
See [metric_families.md](metric_families.md) for the key names.

## Worked example (mini_paired fixture)

`examples/mini_paired` has two sources, each with an equivalence-mode and a
subsumption-mode query whose pools differ:

```bash
PYTHONPATH=src python3 -m biokg_align_kit run-baseline --data-dir examples/mini_paired \
    --task NCIT-DOID --split valid --baseline hybrid_lexical --output /tmp/sub.tsv
PYTHONPATH=src python3 -m biokg_align_kit verify --predictions /tmp/sub.tsv \
    --data-dir examples/mini_paired --split valid          # strict: passes
PYTHONPATH=src python3 -m biokg_align_kit score --predictions /tmp/sub.tsv \
    --data-dir examples/mini_paired --split valid          # 4 queries scored
```

Exchanging the `QueryID`s of one source's two queries while keeping every row's target and
score (the v0.3.2 "swap" probe) is **rejected** by `verify` and by the platform: some targets
are not in the other query's pool (`[off_pool_target]`, naming the query). In lenient mode the
same file scores with warnings: the off-pool rows are dropped and the resulting missing pairs
are filled with `-inf`. Permuting the rows of a valid file changes nothing.
