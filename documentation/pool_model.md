# The pool model

A source entity can contribute more than one query. This document explains how queries are
formed, what the released files show about them, and why everything is keyed by `QueryID`.

## Queries per source

For each source entity of a task the build emits:

- an **equivalence-mode** query when the source has a canonical equivalence target in the
  partner ontology — its preferred pair is that target with relation `equivalent`;
- a **subsumption-mode** query when, in addition, the equivalence target has a direct parent
  or child in the partner ontology that is not one of the source's own equivalence targets — its
  preferred pair is one sampled direct neighbour with `source_subsumed_by_target` (parent) or
  `source_subsumes_target` (child). Since v0.4.0 this sampling is done **per task**, with the
  directions balanced within each task, so a source equivalent in two tasks gets a
  subsumption-mode query in both;
- for a source with several reference mappings and no equivalence: one single-gold query per
  mapping, with the other mappings' targets excluded from each query's pool.

Every query has exactly one preferred (target, relation) pair.

## The pools of one source's queries differ

The two queries of a source are built from **different** 50-candidate pools: the
subsumption-mode pool excludes the equivalence target, and the equivalence-mode pool excludes
the sampled subsumption target (it is a known positive of the source). Both pools contain the
gold target, a structural tier (hierarchical neighbours of the preferred target), and lexical,
semantic-rerank and random tiers. (Kit documentation before 0.4.0 claimed the two queries
share one pool; the build never did this.)

## What the released files show

| File | Train / valid | Test |
|------|---------------|------|
| `tasks/<task>/<split>.cands.tsv` | `QueryID, SrcEntity, TgtCandidates` | same three columns |
| `evaluation/<task>/<split>.answers.tsv`, `.preferred.tsv`, `.graded.tsv` | public | private (organiser package) |
| `evaluation/query_metadata.tsv` (`QueryID, Task, Split, SrcEntity, QueryMode`) | public rows | private rows |

- `QueryID` is opaque (`<task>-<8 hex>`, a keyed hash): it does not encode the mode, and rows
  are **shuffled** per task and split, so neither the identifier nor the row position reveals
  which query of a source is which. The release ships `reports/query_mode_predictability.json`,
  a construction-metadata diagnostic showing that row position predicts the mode no better than
  the majority class. The pools' tier composition, which `tasks/<task>/<split>.composition.json`
  publishes for train and valid, is a different matter: the same report shows it predicts the mode
  well above the majority rate (v0.4.0-rc1 validation: logistic accuracy 0.68–0.75 against 0.50).
- Candidate lists are sorted, so their order carries no information either.
- The query mode is published for train and valid (`evaluation/query_metadata.tsv`) and withheld
  for test. Aggregate counts per task, split and mode are public
  (`reports/reference_construction.json`).
- Because the two queries of a source have different pools, comparing them can reveal
  information about the gold; this is a known property of the pool model (both pools are needed
  for the two queries) and is not hidden by opaque identifiers.

Canonical v0.4.0-rc1 counts (test, equivalence-mode + subsumption-mode): NCIT-DOID 1,851 + 1,837,
SNOMED-FMA 2,972 + 2,968, SNOMED-NCIT 10,337 + 10,296 (30,261 queries).

## Keying contract

Every per-query structure in the kit is keyed by `QueryID`:

- `scoring.load_query_index(paths)` → `{QueryID: Query(query_id, task, source, candidates)}`;
- `scoring.load_answers(path)` → `{QueryID: {(TgtEntity, Relation), ...}}`;
- `scoring.load_preferred_pairs(path)` → `{QueryID: (TgtEntity, Relation)}`;
- `scoring.load_graded_relevance(path)` → `{QueryID: {(TgtEntity, Relation): gain}}`;
- `scoring.load_submission(path, index)` joins submission rows by `QueryID`.

Never key by `SrcEntity`: two queries of the same source would merge. The paired-query tests in
`tests/test_kit_full.py` pin this on `examples/mini_paired/` (2 sources × 2 queries, different
pools).
