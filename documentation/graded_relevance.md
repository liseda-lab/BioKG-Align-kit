# Graded relevance

The Hierarchy-Aware Typed nDCG@10 metric relies on a per-query gain table: a mapping $(\mathrm{target}, \mathrm{relation}) \mapsto g$ with $g \in [0, 1]$. This document specifies the gain ladder and how to construct the corresponding TSV file. The companion document [metric_families.md](metric_families.md) shows where these gains feed into the scoring formula.

## File schema

The `*.graded.tsv` file uses the following schema:

| Column      | Type   | Description                                      |
|-------------|--------|--------------------------------------------------|
| `SrcEntity` | string | Source query entity.                             |
| `QueryID`   | string | Per-source query identifier (`Q0`, `Q1`).        |
| `TgtEntity` | string | Candidate target.                                |
| `Relation`  | string | Relation (one of the canonical three).           |
| `Gain`      | float  | Graded gain, rescaled to $[0, 1]$.               |

Only non-zero gains are emitted; pairs absent from the file have gain $0$. Rows are sorted by `(SrcEntity, QueryID, TgtEntity, relation_canonical_order)` for deterministic output.

## Hierarchy-Aware Typed gain ladder

For preferred pair $(v^*, r^*)$, candidate set $C_q$, and the ELK-augmented target-ontology hierarchy, the gain function $g(\cdot)$ has two parts: an exact-target table at distance $d = 0$, and a hierarchical decay for $d \geq 1$.

### Gold pair (distance $d = 0$)

| Preferred $(v^*, r^*)$ | $g(v^*, \equiv)$ | $g(v^*, \sqsubseteq)$ | $g(v^*, \sqsupseteq)$ |
|------------------------|:----------------:|:---------------------:|:---------------------:|
| $(v^*, \equiv)$        | $1.0$            | $0.6$                 | $0.6$                 |
| $(v^*, \sqsubseteq)$   | $0.0$            | $1.0$                 | $0.0$                 |
| $(v^*, \sqsupseteq)$   | $0.0$            | $0.0$                 | $1.0$                 |

The $0.6$ entries on the equivalence-preferred row capture near-miss credit: an equivalence is "almost" both a $\sqsubseteq$ and an $\sqsupseteq$, so partial credit at those relations on the gold target reflects the right-entity–wrong-relation case.

### Hierarchical partial credit (distance $d \in \{1, 2, 3\}$)

Distances are the BFS-by-level shortest path over `hierarchy.parents` (ancestors) and `hierarchy.children` (descendants). Only entities present in $C_q$ contribute — a system can't rank what it isn't given.

| Preferred $r^*$ | Walk        | Pair receiving credit                 | Gain                |
|-----------------|-------------|---------------------------------------|---------------------|
| $\equiv$        | ancestors   | $(\mathrm{ancestor},\ \sqsubseteq)$   | $\frac{0.6}{d+1}$   |
| $\equiv$        | descendants | $(\mathrm{descendant},\ \sqsupseteq)$ | $\frac{0.6}{d+1}$   |
| $\sqsubseteq$   | ancestors   | $(\mathrm{ancestor},\ \sqsubseteq)$   | $\frac{1.0}{d+1}$   |
| $\sqsupseteq$   | descendants | $(\mathrm{descendant},\ \sqsupseteq)$ | $\frac{1.0}{d+1}$   |

The walk depth is capped at $\mathrm{max\_distance} = 3$, so $d \in \{1, 2, 3\}$.

## Construction with the kit

For a complete release-shape build:

```bash
PYTHONPATH=src python3 -m biokg_align_kit build-graded-relevance \
  --preferred  tasks/NCIT-DOID/valid.preferred.tsv \
  --candidates tasks/NCIT-DOID/valid.cands.tsv \
  --triples    graph/triples.csv \
  --output     tasks/NCIT-DOID/valid.graded.tsv
```

The helper reads only `relation = subclass_of` rows from `triples.csv` (other relations are silently filtered). The hierarchy index is built once and reused across all queries.

The helper is deterministic given the inputs. The public release will ship pre-computed `*.graded.tsv` files for the train and valid splits; the helper exists so participants can sanity-check the released files (by re-running and diffing) and so the kit is self-contained.

## Worked example (mini fixture)

Mini fixture preferred pairs (both `Q0`, equivalence-preferred):

  NCIT:C001  Q0  DOID:D001  equivalent
  NCIT:C002  Q0  DOID:D002  equivalent

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

These four numbers are exactly what `examples/mini/answers/NCIT-DOID.valid.graded.tsv` contains for the `NCIT:C001` query.
