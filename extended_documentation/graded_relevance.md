# Graded relevance

The Hierarchy-Aware Typed nDCG@10 metric relies on a per-query gain
table: a mapping from `(target, relation)` pairs to a non-negative
gain. This document specifies the gain ladder and how to construct
the corresponding TSV file.

The companion document [`metric_families.md`](metric_families.md)
shows where these gains feed into the scoring formula.

## File schema

The `*.graded.tsv` file uses the following schema:

| Column      | Type   | Description                                      |
|-------------|--------|--------------------------------------------------|
| `SrcEntity` | string | Source query entity.                             |
| `QueryID`   | string | Per-source query identifier (`Q0`, `Q1`).        |
| `TgtEntity` | string | Candidate target.                                |
| `Relation`  | string | Relation (one of the canonical three).           |
| `Gain`      | float  | Graded gain. Rescaled to `[0, 1]`.               |

Only non-zero gains are emitted. Pairs absent from the file have
gain 0. Rows are sorted by
`(SrcEntity, QueryID, TgtEntity, relation_canonical_order)` for
deterministic output.

## Hierarchy-Aware Typed gain ladder

For preferred pair `(v*, r*)`, candidate set `C_q`, and the
ELK-augmented target-ontology hierarchy:

### Gold pair (distance 0)

| Preferred `(v*, r*)`             | gain at `(v*, eq)` | at `(v*, ssbt)` | at `(v*, sst)` |
|----------------------------------|--------------------|-----------------|----------------|
| `(v*, equivalent)`               | 1.0                | 0.6             | 0.6            |
| `(v*, source_subsumed_by_target)`| 0.0                | 1.0             | 0.0            |
| `(v*, source_subsumes_target)`   | 0.0                | 0.0             | 1.0            |

The 0.6 entries on the equivalence-preferred row capture
near-miss credit: an equivalence is "almost" both a `ssbt` and an
`sst`, so partial credit at those pairs at the gold target reflects
the right-entity-wrong-relation case.

### Hierarchical partial credit (distance `d ∈ {1, 2, 3}`)

Walks use the BFS-by-level shortest-path distance over
`hierarchy.parents` (for ancestors) and `hierarchy.children` (for
descendants). Only entities present in `C_q` contribute — a system
can't rank what it isn't given.

| Preferred relation | Walk             | Pair receiving credit       | Gain          |
|--------------------|------------------|-----------------------------|---------------|
| `equivalent`       | ancestors        | `(ancestor, ssbt)`          | `0.6/(d+1)`   |
| `equivalent`       | descendants      | `(descendant, sst)`         | `0.6/(d+1)`   |
| `ssbt`             | ancestors        | `(ancestor, ssbt)`          | `1.0/(d+1)`   |
| `sst`              | descendants      | `(descendant, sst)`         | `1.0/(d+1)`   |

The walk depth is capped at `max_distance = 3`.

## Construction with the kit

For a complete release-shape build:

```bash
PYTHONPATH=src python3 -m biokg_align_kit build-graded-relevance \
  --preferred  tasks/NCIT-DOID/valid.preferred.tsv \
  --candidates tasks/NCIT-DOID/valid.cands.tsv \
  --triples    graph/triples.csv \
  --output     tasks/NCIT-DOID/valid.graded.tsv
```

The helper reads only `relation = subclass_of` rows from
`triples.csv` (other relations are silently filtered). The hierarchy
index is built once and reused across all queries.

The helper is deterministic given the inputs. The public release
will ship pre-computed `*.graded.tsv` files for the train and valid
splits; the helper exists so participants can sanity-check the
released files (by re-running and diffing) and so the kit is
self-contained.

## Worked example (mini fixture)

Mini fixture preferred pairs (both Q0, equivalence-preferred):

```text
NCIT:C001  Q0  DOID:D001  equivalent
NCIT:C002  Q0  DOID:D002  equivalent
```

Hierarchy from `triples.csv` (`subclass_of` only, restricted to DOID):

```text
DOID:D001 ⊑ DOID:D000
DOID:D002 ⊑ DOID:D000
DOID:D000 ⊑ DOID:DROOT
DOID:D003 ⊑ DOID:DROOT
```

Candidate set: `{DOID:D000, DOID:D001, DOID:D002, DOID:D003}`.

For the NCIT:C001 query (gold `(D001, eq)`):

- `(D001, eq)`   → `1.0`
- `(D001, ssbt)` → `0.6`
- `(D001, sst)`  → `0.6`
- `(D000, ssbt)` → `0.6 / (1 + 1) = 0.3`  (D000 is the direct
  parent, distance 1)
- D003 is unrelated; no credit. D002 is a sibling; not on the
  ancestor or descendant walk; no credit.

These four numbers are exactly what
`examples/mini/answers/NCIT-DOID.valid.graded.tsv` contains for the
NCIT:C001 query.
