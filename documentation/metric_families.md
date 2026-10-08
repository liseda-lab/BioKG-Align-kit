# Metric families

The BioKG-Align main track reports three metric families. The **preferred-pair** family is the leaderboard primary; the **Hierarchy-Aware Typed nDCG@10** family is the secondary; the **diagnostic** family is kit-only and not on the leaderboard.

All three families average over queries — every query contributes equally to a task's score
regardless of the difficulty of its candidate pool — and the leaderboard number is the
unweighted mean over tasks. Every per-query structure is keyed by the opaque `QueryID`.

## Preferred-pair family

For each query $q$, one $(\mathrm{target}, \mathrm{relation})$ pair is fixed as the **preferred gold pair** by the organiser. The preferred-pair metrics are MRR and Hits@K computed against this one pair:

```math
\text{preferred\_typed\_mrr}(q) = 
\begin{cases}
  \frac{1}{\mathrm{rank}\_q(\text{preferred\_pair}\_q)}
    & \text{if found,} \\ 
  0 & \text{otherwise.} 
\end{cases}
```

```math
\text{preferred\_typed\_hits\_at\_K}(q) = \begin{cases} 
  1 & \text{if } \text{rank}\_q(\text{preferred\_pair}\_q) \leq K, \\ 
  0 & \text{otherwise.}
\end{cases}
```

Keys reported by the kit (per task, and in the `macro` block):

- `preferred_typed_mrr` — the leaderboard **primary** metric;
- `preferred_typed_hits_at_1`, `preferred_typed_hits_at_5`, `preferred_typed_hits_at_10`;
- `median_preferred_typed_rank` — median rank of the preferred pair;
- `preferred_typed_queries` — the number of queries scored (a count).

The headline is the **Macro Preferred Typed MRR**: `preferred_typed_mrr` averaged over the
queries of a task, then over the three tasks (NCIT-DOID, SNOMED-FMA, SNOMED-NCIT).

### Relation Macro-F1 on the Preferred Entity

A diagnostic sub-metric within the preferred-pair family. For each query, the system's top-ranked candidate entity is found by taking the max score across relation types (collapsing the per-$(\mathrm{target}, \mathrm{relation})$ ranking to per-$\mathrm{target}$). Two cases:

- **Top entity $\neq$ preferred target.** The query is excluded from the metric (the metric is **unmeasurable** for this query — not zero).
- **Top entity $=$ preferred target.** The query contributes one observation to a confusion matrix. The predicted relation is the one on the system's top-1 $(\mathrm{target}, \mathrm{relation})$ row. Macro-F1 is computed across relation types.

Kit outputs:

- `preferred_entity_relation_accuracy` — fraction of entity-correct queries on which the relation is also correct.
- `preferred_entity_relation_macro_f1` — macro-F1 across relation types, computed only over entity-correct queries.
- `preferred_entity_relation_queries` — count of entity-correct queries (the support of the F1 calculation).

Reading the support count matters. A high F1 with low support is weaker evidence than a moderate F1 with high support; never report the F1 in isolation.

## Hierarchy-Aware Typed nDCG@10

A graded variant of nDCG@10 with continuous gains over hierarchically-close $(\mathrm{target}, \mathrm{relation})$ pairs. The full construction rule is in [graded_relevance.md](graded_relevance.md); the metric is:

$$
\begin{aligned}
\mathrm{DCG}@K(q)  &= \sum_{i=1}^{K} \frac{\mathrm{gain}_q(p_i)}{\log_2(i + 1)} \\
\mathrm{IDCG}@K(q) &= \max \text{ achievable } \mathrm{DCG}@K \ (\text{top-}K \text{ gains, sorted descending}) \\
\mathrm{nDCG}@K(q) &= \frac{\mathrm{DCG}@K(q)}{\mathrm{IDCG}@K(q)}, \quad = 0 \text{ when } \mathrm{IDCG}@K(q) = 0
\end{aligned}
$$

Where $p_i$ is the $i$-th $(\mathrm{target}, \mathrm{relation})$ pair in the ranked submission for query $q$ and $\mathrm{gain}_q(\cdot)$ is the per-query graded relevance lookup from `evaluation/<task>/<split>.graded.tsv`.

Kit outputs:

- `hierarchy_aware_typed_ndcg_at_10` — macro average across queries.
- `hierarchy_aware_typed_ndcg_at_10_queries` — number of queries with a gain table (every query: the preferred pair always has gain 1.0).

### Gain table (Hierarchy-Aware family)

For preferred pair $(v^*, r^*)$:

| Preferred $(v^*, r^*)$ | gain at $(v^*, \equiv)$ | at $(v^*, \sqsubseteq)$ | at $(v^*, \sqsupseteq)$ |
|----------------------------------|--------------------|-----------------|----------------|
| $(v^*, \equiv)$                  | $1.0$              | $0.6$           | $0.6$          |
| $(v^*, \sqsubseteq)$             | $0.0$              | $1.0$           | $0.0$          |
| $(v^*, \sqsupseteq)$             | $0.0$              | $0.0$           | $1.0$          |

Partial credit for ancestors/descendants at hierarchical distance $d$:

- **Equivalence-preferred:** ancestors of $v^*$ get $\frac{0.6}{d + 1}$ at $\sqsubseteq$; descendants get $\frac{0.6}{d + 1}$ at $\sqsupseteq$.
- **`ssbt`-preferred:** ancestors get $\frac{1.0}{d + 1}$ at $\sqsubseteq$.
- **`sst`-preferred:** descendants get $\frac{1.0}{d + 1}$ at $\sqsupseteq$.

## Diagnostic family (kit-only)

Relation-aware binary relevance against the per-query gold set of the answers file. The
released answers carry a single gold pair per query (the preferred pair), so
`diagnostic_mrr`/`diagnostic_hits_at_*`/`diagnostic_map` coincide with the corresponding
`preferred_typed_*` values; the family is kept for continuity and quick checks.

- `diagnostic_relation_aware_ndcg_at_10` — binary nDCG@10 over the gold pair (positional discount; distinct from the graded `hierarchy_aware_typed_ndcg_at_10`).
- `diagnostic_mrr`, `diagnostic_hits_at_{1,5,10}` — reciprocal-rank / hit-rate of the gold pair.
- `diagnostic_map` — average precision over the gold set.
- `diagnostic_top1_relation_macro_f1` — Macro-F1 over the canonical 3-relation taxonomy using the top-1 $(\mathrm{target}, \mathrm{relation})$ row as the predicted positive.

These are for fast local iteration and never enter the leaderboard; always report the headline `preferred_typed_*` and `hierarchy_aware_typed_*` keys alongside them.

## Worked numbers

For a single equivalence-preferred query with candidates `{T1, T2, T3, T4}`, gold `(T2, eq)`, and a ranking that puts `(T2, eq)` at position 1:

- `preferred_typed_mrr = 1/1 = 1.0`
- `preferred_typed_hits_at_1 = 1`

If the ranking instead puts `(T2, eq)` at position 3:

- `preferred_typed_mrr = 1/3 ≈ 0.333`
- `preferred_typed_hits_at_1 = 0`, `hits_at_5 = 1`

Under H-nDCG, suppose `T1` is the direct parent of `T2` (distance 1 ancestor under `eq`-preferred). The gain table has:

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

The bundled `examples/mini_paired/` fixture's `evaluation/NCIT-DOID/valid.graded.tsv` is a byte-level worked example — open it up and step through the gains by hand for each query.

## Count metrics and the macro

`biokg_align_kit.scoring.is_count_metric(key)` is the shared registry: a key is a **count**
iff it is exactly `queries` or ends in `_queries`, `_count`, `_scored`, `_expected` or
`_warnings`; every other key is a **rate**. `macro_average_tasks` averages rates over the tasks
with at least one scored query and sums counts over every requested task, emitting
`<key>_sum`, `<key>_mean` and the bare key (= the sum), plus `tasks` and `tasks_scored`.

Lenient local scoring adds the counts `queries_expected`, `queries_scored` and
`submission_warnings` to each task (see [submission_scoring.md](submission_scoring.md)).

## Organiser-only diagnostics

Organiser reports additionally carry `entity_only_{mrr,hits_at_1,hits_at_5,hits_at_10}`,
`relation_{accuracy,macro_f1}_on_gold_entity_ungated` (argmax relation on the preferred entity
for every query, whichever entity ranks first) and
`hierarchy_aware_typed_ndcg_at_10__{equivalence_only,subsumption_only}` (split by the private
query mode). They are never part of the leaderboard.

## Baseline tables: repeat runs

The organiser baseline tables (`baseline_results.json`, `baseline_results_macro.json`,
`baseline_results.md`) report **run 0** as the canonical number of every system and, for the
stochastic systems (`random`, the four KGE models, the trained text ranker, the cross-encoder
and the two support-augmented rankers), a `repeats` block with every run's flat metrics, their
mean and their **sample** standard deviation (`ddof = 1`) over runs with seeds
`benchmarks.seed + i`. The macro of each run is computed first and mean/std are taken across
the per-run macros. Hyperparameters are selected once on run 0 and frozen, so the spread
measures initialisation, batching and epoch-selection randomness only. Deterministic systems
(`exact_lexical`, `hybrid_lexical`, `word_embedding_based`, `logmap`) run once.

## Local-vs-leaderboard reminder

The kit's `score` reads `evaluation/<task>/<split>.answers.tsv` and its siblings
`<split>.preferred.tsv` and `<split>.graded.tsv` (all three are required and validated) and
`evaluation/query_metadata.tsv`. Your local `preferred_typed_mrr` and
`hierarchy_aware_typed_ndcg_at_10` are computed by exactly the same code the platform runs; a
difference from the leaderboard comes from the split (train/valid locally, private test on the
platform) or from lenient-mode fallbacks (local only, always announced) — never from the
metric implementation.
