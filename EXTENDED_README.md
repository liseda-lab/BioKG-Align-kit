# BioKG-Align Kit

This is the public starting kit for BioKG-Align, a biomedical knowledge graph alignment challenge.
It is the part participants should use. It does not generate the official dataset and it does not
contain hidden test labels.

BioKG-Align asks a simple question over a structured biomedical graph: given a source entity and a
fixed list of candidate targets from another ontology, which candidate is correct, and what relation
does it have to the source?

The relation labels are:

- `equivalent`
- `source_subsumed_by_target`
- `source_subsumes_target`

For every query, systems submit scored candidate-relation pairs. A prediction is relevant only when
both the target entity and the relation type are correct.

**Track scope.** This kit covers the **main track** (typed candidate ranking) only. The competition's
**complex track** (OWL class expression generation) is a separate problem with its own data and
evaluation pipeline — see `docs/complex_track.md` for context.

## What is in this kit

- a local scorer with the diagnostic, preferred-pair, and Hierarchy-Aware
  Typed nDCG@10 metric families;
- a submission validator (block-format);
- reference baselines (`random` and `hybrid_lexical`);
- three example fixtures: `mini` (compact, human-readable),
  `canonical` (50 candidates per query — production shape), and
  `mini_paired` (exercises paired-query Q0 + Q1 per source);
- a Datalog reader for `graph/facts.dl` / `graph/rules.dl`
  (`biokg_align_kit.datalog`);
- a `build-graded-relevance` helper for the Hierarchy-Aware gain
  table;
- extended specifications in `documentation/`;
- the challenge website draft under `docs/`.

The extended specifications under `documentation/` cover the submission
format end-to-end (`submission_format.md`, `block_scoring.md`), the
metric families with worked examples (`metric_families.md`,
`graded_relevance.md`), the pool model (`pool_model.md`), and the
public file inventory (`data_card.md`).

The official dataset will be distributed separately as a public data artifact. Participants should
download that artifact, train their methods, and use this kit to validate submissions and reproduce
baseline formats.

## Install

From this repository:

```bash
python3 -m pip install -e .
```

You can also run the package without installation:

```bash
PYTHONPATH=src python3 -m biokg_align_kit --help
```

### Dataset compatibility

This kit (`0.2.0`) targets the **BioKG-Align v0.2.0** dataset build family. The kit's scorer,
validator, and baseline assumptions reflect the schema and file layout of that build; running
against a materially different build may produce warnings or scoring divergences. The
compatibility string is informational and not enforced at install time — check it with:

```bash
PYTHONPATH=src python3 -m biokg_align_kit --version
```

If the dataset's `release_manifest.json` reports a different version, inspect the dataset
CHANGELOG for schema changes before assuming kit output is correct.

## Try the example

Score the example hybrid_lexical baseline against the tiny answer file:

```bash
PYTHONPATH=src python3 -m biokg_align_kit run-baseline \
  --data-dir examples/mini \
  --task NCIT-DOID \
  --split valid \
  --baseline hybrid_lexical \
  --output /tmp/biokg-align-mini.tsv

PYTHONPATH=src python3 -m biokg_align_kit score \
  --predictions /tmp/biokg-align-mini.tsv \
  --answers examples/mini/answers/NCIT-DOID.valid.answers.tsv
```

Validate a submission against candidate files:

```bash
PYTHONPATH=src python3 -m biokg_align_kit verify \
  --predictions /tmp/biokg-align-mini.tsv \
  --candidates examples/mini/tasks/NCIT-DOID/valid.cands.tsv \
  --candidates-per-query 0
```

The `--candidates-per-query 0` flag disables the candidate-count check;
the mini fixture has 4 candidates per query rather than the official 50.
Omit the flag (or pass `--candidates-per-query 50`) when validating
against the real challenge data.

Summarize a data directory:

```bash
PYTHONPATH=src python3 -m biokg_align_kit summarize-data --data-dir examples/mini
```

### Canonical-shape fixture

For exercising the kit against realistic inputs without downloading the
public dataset, `examples/canonical/` ships a fixture with the official
`|C_q| = 50` candidates per query. The mini fixture stays small for
human readability; the canonical fixture exists for end-to-end testing
and benchmarking:

```bash
PYTHONPATH=src python3 -m biokg_align_kit run-baseline \
  --data-dir examples/canonical \
  --task NCIT-DOID \
  --split valid \
  --baseline hybrid_lexical \
  --output /tmp/biokg-align-canonical.tsv

PYTHONPATH=src python3 -m biokg_align_kit verify \
  --predictions /tmp/biokg-align-canonical.tsv \
  --candidates examples/canonical/tasks/NCIT-DOID/valid.cands.tsv

PYTHONPATH=src python3 -m biokg_align_kit score \
  --predictions /tmp/biokg-align-canonical.tsv \
  --answers examples/canonical/answers/NCIT-DOID.valid.answers.tsv
```

The canonical fixture validates without warnings at the default
`--candidates-per-query 50` and exercises all three metric families (diagnostic, preferred-pair,
and Hierarchy-Aware Typed nDCG@10). Its
content is generated by `examples/canonical/_build_canonical_fixture.py`,
which is deterministic — re-running it produces byte-identical output.

## Submission format

> **Submission format.** The participant submission format is the
> 4-column block-scoring TSV. The `verify` command validates
> submissions against the block-scoring contract. See the
> "Block-scoring layout" subsection below for the per-row contract.

Predictions are TSV files with exactly four columns:

```text
SrcEntity    TgtEntity    Relation    Score
```

- `SrcEntity` — the source entity from the query row in the candidates file.
- `TgtEntity` — one of the candidate target entities provided for that query.
- `Relation` — exactly one of `equivalent`, `source_subsumed_by_target`,
  `source_subsumes_target`.
- `Score` — a real-valued confidence score. Higher scores rank earlier.

### Block-scoring layout

Rows are grouped positionally into per-query blocks of size
`candidate_count × |submission.relations|` (canonical 50 × 3 = 150).
Submission row `i` belongs to block `i // block_size`, and block `k`
corresponds to the k-th row of the public `tasks/<task>/test.cands.tsv`
file in canonical task order. The scorer recovers `(SrcEntity, QueryID)`
positionally — the public `test.cands.tsv` no longer carries a `QueryID`
column, but the lockstep row order against the private
answers file restores it.

Within a block the 150 rows can appear in any order; the scorer indexes
by `(TgtEntity, Relation)`.

The official platform applies the following strictness rules per
`documentation.md §4.4`:

| Condition                                            | Behaviour       |
|------------------------------------------------------|-----------------|
| Total row count ≠ `N_queries × block_size`           | Fatal           |
| Block contains rows for a disagreeing `SrcEntity`    | Fatal           |
| Row's `Relation` not in `submission.relations`       | Fatal           |
| `Score` not parseable as float                       | Fatal           |
| Duplicate `(TgtEntity, Relation)` within a block     | Warn; max-merge |
| Missing canonical `(TgtEntity, Relation)`            | Warn; score 0.0 |
| Row's `TgtEntity` not in that query's candidate set  | Silent filter   |

The kit's `verify` command enforces the same rules locally, so
participants can catch issues before upload.

For the canonical build (30,614 test queries across NCIT-DOID,
SNOMED-FMA, SNOMED-NCIT), a complete submission is
`30,614 × 150 = 4,592,100` rows.

### Per-query row count

Per the challenge spec (paper §2.1), a well-formed submission contains
exactly `|C_q| × |R_A| = 50 × 3 = 150` rows per query: every candidate
target × every relation. A submission that omits some pairs is still
accepted: the platform's evaluator assigns score `0.0` to missing pairs,
which effectively ranks them at the bottom of the block. In practice this
means any pair you don't score is forfeited.

The local validator (`verify`) emits a warning, not an error, when it
detects missing pairs. The bundled `examples/mini` fixture
uses 4 candidates per query rather than 50, so pass
`--candidates-per-query 0` when validating against the mini fixture to
suppress the count warning. For end-to-end testing at the official shape,
use `examples/canonical/` (50 candidates per query) instead — see the
"Canonical-shape fixture" section above.

### Tie-break ordering

When two prediction rows for the same query share the same score, they
are ranked by ascending `TgtEntity` first, then by relation in the fixed
order `equivalent ≺ source_subsumed_by_target ≺ source_subsumes_target`
(paper §2.1). The local scorer implements this explicitly; participants
who post-process their submissions in another tool should ensure the
same convention is preserved if reproducibility against the leaderboard
matters.

### Local scores ≠ leaderboard scores

> **Important.** The local scorer (`score`) reports a simplified set of
> diagnostic metrics — untyped MRR, binary-relevance nDCG@10, top-1
> macro-F1, etc. — alongside the headline metric families when the
> required files are present. The full leaderboard uses the
> *Macro Preferred Relation-Aware MRR* primary metric and the
> *Macro Hierarchy-Aware Typed nDCG@10* secondary metric defined in
> paper §1.5. Local kit scores will be reported under the
> `preferred_typed_*` and `hierarchy_aware_typed_*` keys when the
> corresponding `*.preferred.tsv` and `*.graded.tsv` files are
> available; the `diagnostic_*` keys remain for fast iteration only.

### Headline metric files

The headline metrics need two extra files alongside the candidates:

- `*.preferred.tsv` — one preferred `(target, relation)` gold per query
  (schema: `SrcEntity QueryID TgtEntity Relation`). Used by *Preferred
  Relation-Aware MRR* and Hits@K.
- `*.graded.tsv` — graded-relevance gains over `(candidate, relation)`
  pairs (schema: `SrcEntity QueryID TgtEntity Relation Gain`). Used by
  *Hierarchy-Aware Typed nDCG@10*.

The official public data artifact will ship both files for the train and
valid splits (the test versions stay server-side). A pre-computed
graded-relevance archive is planned for distribution via Zenodo
alongside the dataset release; until then you can build the file
yourself with `build-graded-relevance` (see below).

By default the scorer auto-discovers these files next to the answers
file using the conventional naming: `NCIT-DOID.valid.answers.tsv` →
`NCIT-DOID.valid.preferred.tsv` and `NCIT-DOID.valid.graded.tsv`.
Use the explicit `--preferred` and `--graded` flags to override.

### Building the graded-relevance file yourself

```bash
PYTHONPATH=src python3 -m biokg_align_kit build-graded-relevance \
  --preferred answers/NCIT-DOID.valid.preferred.tsv \
  --candidates tasks/NCIT-DOID/valid.cands.tsv \
  --triples graph/triples.csv \
  --output answers/NCIT-DOID.valid.graded.tsv
```

The construction rule is fully specified in paper §1.5 and is
deterministic given the preferred-pair file and the target-ontology
hierarchy (read from `subclass_of` triples). Re-running the helper on
the same inputs produces byte-identical output.

## Website

The website draft is in `docs/` and can be served by GitHub Pages from the `docs` folder.
