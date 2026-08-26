# Block scoring

Submission rows are grouped positionally into per-query blocks. The scoring pipeline never reads a `QueryID` column from the submission itself — that column doesn't exist in the four-column contract. Instead, the scorer walks the public `test.cands.tsv` file in row order and assigns submission rows to blocks by position.

## Block layout

Let `candidate_count` be the number of candidate target entities per query (canonical: 50) and $R_A$ be the set of relation types scored per query:

$$
R_{A} = \{\ \text{equivalent},\ \text{source_subsumed_by_target},\ \text{source_subsumes_target}\ \}.
$$

In description-logic shorthand, $R_A = \{\equiv,\ \sqsubseteq,\ \sqsupseteq\}$, with cardinality $|R_A| = 3$. Then:

$$
\begin{aligned}
\mathrm{block\_size}
  &= \mathrm{candidate\_count} \times |R_A| \\
  &= 50 \times 3 \\
  &= 150.
\end{aligned}
$$

For a submission file with $N_{\mathrm{rows}}$ rows and a `test.cands.tsv` with $N_{\mathrm{queries}}$ rows:

$$
N_{\mathrm{rows}} = N_{\mathrm{queries}} \times \mathrm{block\_size}.
$$

This means that row $i$ of the submission ($i$ 0-indexed, after stripping the header) belongs to block $\left\lfloor i / \mathrm{block\_size} \right\rfloor$. Block $k$ corresponds to row $k$ of `test.cands.tsv`. The `SrcEntity` column in every submission row of block $k$ must equal the `SrcEntity` of row $k$ of `test.cands.tsv`.

The 150 rows within a single block can appear in any order — the scorer indexes by `(TgtEntity, Relation)` and the order does not affect any metric. The block boundaries are what matter.

## Canonical task ordering

The single submission TSV covers all three task pairs. The canonical task order is fixed:

$$
\big(\ \text{NCIT-DOID},\ \text{SNOMED-FMA},\ \text{SNOMED-NCIT}\ \big).
$$

This is the order in which per-task `test.cands.tsv` files are conceptually concatenated to define the global query order. Under the canonical build at fraction=1.0 the global counts are:

| Quantity | Value |
|----------|-------|
| `N_queries` (test) | `29,490` |
| `block_size` | `150` |
| `N_rows` total | `4,423,500` |

A correctly-shaped submission is therefore a 4,423,500-row file (plus one header line).

Per task, in the canonical concatenation order:

| Task | test queries | submission rows ($\times 150$) |
|------|-------------:|--------------------------:|
| NCIT-DOID   | 3,688  | 553,200   |
| SNOMED-FMA  | 5,153  | 772,950   |
| SNOMED-NCIT | 20,649 | 3,097,350 |
| **Total**   | **29,490** | **4,423,500** |

## Strictness rules

The platform scorer enforces the following rules row-by-row and block-by-block. The kit's `biokg-align-kit verify` runs the same checks locally.

| Condition | Behaviour |
|-----------|-----------|
| Total row count $\neq N_{\mathrm{queries}} \times \mathrm{block\_size}$ | **Fatal**. The submission is rejected. |
| Block $k$ contains a row whose `SrcEntity` disagrees with `test.cands.tsv` row $k$ | **Fatal**. |
| Row's `Relation` is not in the canonical relation list | **Fatal**. |
| Row's `Score` is not parseable as a finite float | **Fatal**. |
| Duplicate $(\mathrm{TgtEntity}, \mathrm{Relation})$ pair within a block | **Warn**; the scorer keeps the maximum score per pair. |
| Missing canonical $(\mathrm{TgtEntity}, \mathrm{Relation})$ pair within a block | **Warn**; the scorer assigns score $-\infty$ (last-rank for any score range, including negative scores). |
| Row's `TgtEntity` is not in that query's candidate set | **Silent filter**. The row is dropped; if a canonical pair is missing as a consequence, the missing-pair warning surfaces it. |

Fatal conditions stop scoring immediately and return an error to the participant. Warning conditions produce diagnostics but allow scoring to proceed.

## How a block is scored

Given a single block of submission rows and the corresponding row of `test.cands.tsv` (which provides the candidate set), the scorer:

1. Reads `TgtCandidates` from the cands row to obtain the canonical 50-element candidate set $C_q$ for this query.
2. Builds a $(\mathrm{TgtEntity}, \mathrm{Relation}) \rightarrow \mathrm{Score}$ map from the block. Duplicates max-merge; missing pairs default to $-\infty$ (genuinely last-rank whatever the submitted score range).
3. Materialises a 150-row ranked list by walking the canonical ordering $(\mathrm{candidate}, \mathrm{relation})$ for $\mathrm{candidate} \in \mathrm{sorted}(C_q)$ and $\mathrm{relation} \in R_A$ (in canonical order), and looking up each pair's score.
4. Sorts the 150 rows by descending `Score` with the deterministic tie-break (`TgtEntity` ascending, then relation order).
5. Computes the per-query contribution to each metric family using the per-`(SrcEntity, QueryID)` gold and gain tables — see [metric_families.md](metric_families.md).

**The platform aggregates the per-query contributions into the macro-averaged scores reported on the leaderboard.**

## Worked layout (3-query, 4-candidate toy)

For a toy `test.cands.tsv` with 3 queries and 4 candidates per query, `block_size = 4 × 3 = 12` and the full submission has `3 × 12 = 36` rows. Schematically:

```text
test.cands.tsv (3 rows):                Submission (36 rows after the header):
  Row 0: NCIT:A  ['T1','T2','T3','T4']    Rows  0..11 (block 0): SrcEntity = NCIT:A
  Row 1: NCIT:B  ['T1','T2','T3','T4']    Rows 12..23 (block 1): SrcEntity = NCIT:B
  Row 2: NCIT:C  ['T1','T2','T3','T4']    Rows 24..35 (block 2): SrcEntity = NCIT:C
```

Any block-0 row with `SrcEntity` $\not=$ `NCIT:A` is fatal; the 12 block-0 rows can appear in any internal order.

The bundled `examples/mini/` fixture uses this exact toy shape (with 2 queries × 4 candidates × 3 relations = 24 submission rows).

## Local validation

```bash
PYTHONPATH=src python3 -m biokg_align_kit verify \
  --predictions submission.tsv \
  --candidates tasks/NCIT-DOID/valid.cands.tsv \
  --candidates-per-query 50
```

For a partial test (one task at a time, or one split), pass the cands file you're validating against and the corresponding submission slice. For the full release-shape submission, concatenate the three test cands files in canonical task order and validate against the result.
