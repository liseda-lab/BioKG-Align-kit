# Metric families

The BioKG-Align main track reports three metric families. The
**preferred-pair** family is the leaderboard primary (paper §1.5);
the **Hierarchy-Aware Typed nDCG@10** family is the secondary; the
**diagnostic** family is kit-only and not on the leaderboard.

All three families are macro-averaged across queries — every query
contributes equally to the final score regardless of the size of its
gold set or the difficulty of its candidate pool.

## Preferred-pair family (paper §1.5 primary)

For each query `q`, one `(target, relation)` pair is fixed as the
**preferred gold pair** by the organiser. The preferred-pair metrics
are MRR and Hits@K computed against this one pair:

```text
preferred_typed_mrr(q)        = 1 / rank_q(preferred_pair_q)        if found, else 0
preferred_typed_hits_at_K(q)  = 1 if rank_q(preferred_pair_q) ≤ K, else 0
```

The macro-averaged scores reported by the kit:

- `preferred_typed_mrr`
- `preferred_typed_hits_at_1`
- `preferred_typed_hits_at_5`
- `preferred_typed_hits_at_10`

### Relation Macro-F1 on the Preferred Entity

A diagnostic sub-metric within the preferred-pair family. For each
query, the system's top-ranked candidate entity is found by taking
the max score across relation types (collapsing the per-`(target,
relation)` ranking to per-`target`). Two cases:

- **Top entity ≠ preferred target.** The query is excluded from the
  metric (the metric is **unmeasurable** for this query — not zero).
- **Top entity = preferred target.** The query contributes one
  observation to a confusion matrix. The predicted relation is the
  one on the system's top-1 `(target, relation)` row. Macro-F1 is
  computed across relation types.

Kit outputs:

- `preferred_entity_relation_accuracy` — fraction of entity-correct
  queries on which the relation is also correct.
- `preferred_entity_relation_macro_f1` — macro-F1 across relation
  types, computed only over entity-correct queries.
- `preferred_entity_relation_queries` — count of entity-correct
  queries (the support of the F1 calculation).

Reading the support count matters. A high F1 with low support is
weaker evidence than a moderate F1 with high support; never report
the F1 in isolation.

## Hierarchy-Aware Typed nDCG@10 (paper §1.5 secondary)

A graded variant of nDCG@10 with continuous gains over
hierarchically-close `(target, relation)` pairs. The full
construction rule is in [`graded_relevance.md`](graded_relevance.md);
the metric is:

```text
DCG@K(q)  = Σ_{i=1..K} gain_q(p_i) / log_2(i + 1)
IDCG@K(q) = max achievable DCG@K (top-K gains, sorted descending)
nDCG@K(q) = DCG@K(q) / IDCG@K(q),   = 0 when IDCG@K(q) = 0
```

Where `p_i` is the i-th `(target, relation)` pair in the ranked
submission for query `q` and `gain_q(·)` is the per-query graded
relevance lookup from `*.graded.tsv`.

Kit outputs:

- `hierarchy_aware_typed_ndcg_at_10` — macro average across queries.
- `hierarchy_aware_typed_ndcg_at_10_queries` — number of queries that
  had at least one positive-gain pair (i.e., the metric's support).

### Gain table (Hierarchy-Aware family)

For preferred pair `(v*, r*)`:

| Preferred `(v*, r*)`             | gain at `(v*, eq)` | at `(v*, ssbt)` | at `(v*, sst)` |
|----------------------------------|--------------------|-----------------|----------------|
| `(v*, equivalent)`               | 1.0                | 0.6             | 0.6            |
| `(v*, source_subsumed_by_target)`| 0.0                | 1.0             | 0.0            |
| `(v*, source_subsumes_target)`   | 0.0                | 0.0             | 1.0            |

Partial credit for ancestors/descendants at hierarchical distance `d`:

- **Equivalence-preferred:** ancestors of `v*` get `0.6 / (d + 1)` at
  ssbt; descendants get `0.6 / (d + 1)` at sst.
- **ssbt-preferred:** ancestors get `1.0 / (d + 1)` at ssbt.
- **sst-preferred:** descendants get `1.0 / (d + 1)` at sst.

## Diagnostic family (kit-only)

Set-based binary relevance against the full gold set. Useful for fast
local iteration but **not** comparable to the leaderboard.

- `diagnostic_relation_aware_ndcg_at_10` — binary nDCG@10 against any
  gold pair.
- `diagnostic_mrr`, `diagnostic_hits_at_{1,5,10}` — set-based.
- `diagnostic_map` — Mean Average Precision over the gold set.
- `diagnostic_top1_relation_macro_f1` — Macro-F1 over the canonical
  3-relation taxonomy using the top-1 row as the predicted positive.

A high diagnostic score with a low `preferred_typed_*` score signals
a ranking that hits the gold target but with the wrong relation — a
common failure mode for under-typed candidate scoring. Always report
the headline `preferred_typed_*` and `hierarchy_aware_typed_*` keys
alongside the diagnostic numbers.

## Worked numbers

For a single equivalence-preferred query with candidates
`{T1, T2, T3, T4}`, gold `(T2, eq)`, and a ranking that puts
`(T2, eq)` at position 1:

- `preferred_typed_mrr = 1/1 = 1.0`
- `preferred_typed_hits_at_1 = 1`

If the ranking instead puts `(T2, eq)` at position 3:

- `preferred_typed_mrr = 1/3 ≈ 0.333`
- `preferred_typed_hits_at_1 = 0`, `hits_at_5 = 1`

Under H-nDCG, suppose `T1` is the direct parent of `T2` (distance 1
ancestor under `eq`-preferred). The gain table has:

```text
(T2, eq)   → 1.0
(T2, ssbt) → 0.6
(T2, sst)  → 0.6
(T1, ssbt) → 0.6 / (1 + 1) = 0.3
```

If the ranking is `(T2, eq) > (T1, ssbt) > ... > (T2, ssbt) > (T2, sst)`:

```text
DCG@10  = 1.0/log2(2) + 0.3/log2(3) + ... = 1.0 + 0.189 + (later gains)
IDCG@10 = same gains sorted descending = 1.0 + 0.6/log2(3) + 0.6/log2(4) + 0.3/log2(5) + ...
nDCG@10 = DCG@10 / IDCG@10
```

The bundled `examples/mini_paired/` fixture's
`NCIT-DOID.valid.graded.tsv` file is a byte-level worked example —
open it up and step through the gains by hand for each query.

## Local-vs-leaderboard reminder

The kit's `score` CLI auto-discovers `*.preferred.tsv` and
`*.graded.tsv` next to the answers file and emits the headline
families when those siblings are present. If they're missing, only
the `diagnostic_*` family is reported, with a note on stderr
explaining which optional files were not found.

If your local `preferred_typed_mrr` and
`hierarchy_aware_typed_ndcg_at_10` are present, they are computed by
exactly the same code paths the platform runs. Any divergence from
the leaderboard score is therefore attributable to the gap between
the public train/valid split (which the kit scores against) and the
private test split (which the platform scores against) — not to the
metric implementation.
