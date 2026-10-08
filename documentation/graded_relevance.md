# Graded relevance

The Hierarchy-Aware Typed nDCG@10 metric relies on a per-query gain table: a mapping $(\mathrm{target}, \mathrm{relation}) \mapsto g$ with $g \in [0, 1]$. This document specifies the gain ladder and how to construct the corresponding TSV file. The companion document [metric_families.md](metric_families.md) shows where these gains feed into the scoring formula.

## File schema

The `*.graded.tsv` file uses the following schema:

`evaluation/<task>/<split>.graded.tsv`:

| Column      | Type   | Description                                      |
|-------------|--------|--------------------------------------------------|
| `QueryID`   | string | The query's opaque identifier.                   |
| `SrcEntity` | string | Source query entity.                             |
| `TgtEntity` | string | Candidate target (always a member of the pool).  |
| `Relation`  | string | Relation (one of the canonical three).           |
| `Gain`      | float  | Graded gain in (0, 1], six decimals.             |

Only non-zero gains are emitted; pairs absent from the file have gain $0$; the preferred pair
always has gain 1.0. Rows are sorted by `(QueryID, TgtEntity, relation order)`.

## Hierarchy-Aware Typed gain ladder

For preferred pair $(v^*, r^*)$, candidate set $C_q$, and the ELK-augmented target-ontology hierarchy, the gain function $g(\cdot)$ has two parts: an exact-target table at distance $d = 0$, and a hierarchical decay for $d \geq 1$.

### Gold pair (distance $d = 0$)

| Preferred $(v^*, r^*)$ | $g(v^*, \equiv)$ | $g(v^*, \sqsubseteq)$ | $g(v^*, \sqsupseteq)$ |
|------------------------|:----------------:|:---------------------:|:---------------------:|
| $(v^*, \equiv)$        | $1.0$            | $g_{eq}$              | $g_{eq}$              |
| $(v^*, \sqsubseteq)$   | $0.0$            | $1.0$                 | $0.0$                 |
| $(v^*, \sqsupseteq)$   | $0.0$            | $0.0$                 | $1.0$                 |

$g_{eq}$ is the **equivalence partial gain**, canonically $0.6$ (parameter
`equivalence_partial_gain` of `hierarchy.compute_graded_relevance`, CLI
`--equivalence-partial-gain`). It captures near-miss credit: an equivalence is "almost" both a
$\sqsubseteq$ and an $\sqsupseteq$, so partial credit at those relations on the gold target
reflects the right-entity–wrong-relation case.

### Hierarchical partial credit (distance $d \in \{1, 2, 3\}$)

Distances are the BFS-by-level shortest path over `hierarchy.parents` (ancestors) and `hierarchy.children` (descendants). Only entities present in $C_q$ contribute — a system can't rank what it isn't given.

| Preferred $r^*$ | Walk        | Pair receiving credit                 | Gain                |
|-----------------|-------------|---------------------------------------|---------------------|
| $\equiv$        | ancestors   | $(\mathrm{ancestor},\ \sqsubseteq)$   | $\frac{g_{eq}}{d+1}$ |
| $\equiv$        | descendants | $(\mathrm{descendant},\ \sqsupseteq)$ | $\frac{g_{eq}}{d+1}$ |
| $\sqsubseteq$   | ancestors   | $(\mathrm{ancestor},\ \sqsubseteq)$   | $\frac{1.0}{d+1}$   |
| $\sqsupseteq$   | descendants | $(\mathrm{descendant},\ \sqsupseteq)$ | $\frac{1.0}{d+1}$   |

The walk depth is capped at $\mathrm{max\_distance} = 3$ (CLI `--max-distance`), so
$d \in \{1, 2, 3\}$. The organiser builds the released files with this kit function over the
ELK-augmented target hierarchy; the sensitivity study of the release varies
$g_{eq} \in \{0.3, 0.6, 0.9\}$ and the cap $\in \{1, 3, 5\}$.

## Construction with the kit

For a complete release-shape build:

```bash
PYTHONPATH=src python3 -m biokg_align_kit build-graded-relevance \
  --preferred  evaluation/NCIT-DOID/valid.preferred.tsv \
  --candidates tasks/NCIT-DOID/valid.cands.tsv \
  --triples    graph/triples.csv \
  --output     /tmp/NCIT-DOID.valid.graded.tsv \
  [--max-distance 3] [--equivalence-partial-gain 0.6]
```

The helper reads only `relation = subclass_of` rows from `triples.csv` (other relations are silently filtered). The hierarchy index is built once and reused across all queries.

The helper is deterministic given the inputs. The public release ships the train and valid
graded files under `evaluation/<task>/`; the helper lets participants re-derive them. (The
organiser's files use the private ELK-augmented hierarchy, which can contain inferred edges that
`graph/triples.csv` also carries; the public triples are the participant-side source.)

## Worked example (mini fixture)

Mini fixture preferred pairs (`evaluation/NCIT-DOID/valid.preferred.tsv`, both
equivalence-preferred):

  NCIT-DOID-5650697f  NCIT:C002  DOID:D002  equivalent
  NCIT-DOID-e4b065eb  NCIT:C001  DOID:D001  equivalent

Hierarchy from `triples.csv` (`subclass_of` only, restricted to DOID):

  DOID:D001 $\sqsubseteq$ DOID:D000
  DOID:D002 $\sqsubseteq$ DOID:D000
  DOID:D000 $\sqsubseteq$ DOID:DROOT
  DOID:D003 $\sqsubseteq$ DOID:DROOT

Candidate set: `{DOID:D000, DOID:D001, DOID:D002, DOID:D003}`.

For the `NCIT:C001` query (gold `(D001, eq)`):

- `(D001, eq)` $\rightarrow$ `1.0`
- `(D001, ssbt)` $\rightarrow$ `0.6`
- `(D001, sst)` $\rightarrow$ `0.6`
- `(D000, ssbt)` $\rightarrow$ `0.6 / (1 + 1) = 0.3` (D000 is the direct parent, distance 1)
- D003 is unrelated; no credit. D002 is a sibling — not on the ancestor or descendant walk; no credit.

These four numbers are exactly what `examples/mini/evaluation/NCIT-DOID/valid.graded.tsv`
contains for the `NCIT:C001` query.
