# BioKG-Align Kit

This is the public starting kit for BioKG-Align, a biomedical knowledge graph alignment challenge. It is the part participants should use. It does not generate the official dataset and it does not contain hidden test labels.

The main track asks a single question over a unified biomedical graph: given a source entity and a fixed list of candidate targets from another ontology, **which candidate is correct, and what relation does it hold to the source?** The relations are `equivalent`, `source_subsumed_by_target`, and `source_subsumes_target`. For every query a system submits scored candidate–relation pairs, and a prediction counts only when **both** the target entity and the relation are correct.

**Track scope.** This kit covers the **main track** (typed candidate ranking); the competition's **complex track** (OWL class-expression generation) is detailed under [documentation/complex_track.md](documentation/complex_track.md).

## What's in the kit

- a local scorer with the preferred, Hierarchy-Aware Typed nDCG@10, and diagnostic metric families
- a block-format submission validator (`verify`)
- the simple `random` and `lexical` baselines
- three example fixtures:
  - `mini` - compact, human-readable
  - `canonical` - 50 candidates per query, production shape
  - `mini_paired` - paired equivalence and subsumption candidates (per source)
- a Datalog reader for `graph/facts.dl` / `graph/rules.dl` (`biokg_align_kit.datalog`)
- a `build-graded-relevance` helper for the Hierarchy-Aware gain table
- specifications found under [documentation/](documentation/README.md)

The [official dataset](#) is distributed separately as a public data artifact. Participants should download that artifact, train their methods, and use this kit to validate submissions and reproduce baseline formats.

## Install

```bash
python3 -m pip install -e .
```

Or run without installing:

```bash
PYTHONPATH=src python3 -m biokg_align_kit --help
```

## Quickstart

Generate the `hybrid_lexical` baseline on the bundled `mini` fixture, then score and validate it:

```bash
PYTHONPATH=src python3 -m biokg_align_kit run-baseline \
  --data-dir examples/mini --task NCIT-DOID --split valid \
  --baseline hybrid_lexical --output /tmp/mini.tsv

PYTHONPATH=src python3 -m biokg_align_kit score \
  --predictions /tmp/mini.tsv \
  --answers examples/mini/answers/NCIT-DOID.valid.answers.tsv

PYTHONPATH=src python3 -m biokg_align_kit verify \
  --predictions /tmp/mini.tsv \
  --candidates examples/mini/tasks/NCIT-DOID/valid.cands.tsv \
  --candidates-per-query 0
```

The `mini` fixture has 4 candidates per query, so `--candidates-per-query 0` disables the count check; use the default `--candidates-per-query 50` against the real data. To exercise the kit at the production shape ($|C_q| = 50$) without downloading the dataset, use the deterministic `examples/canonical/` fixture — same three commands with `--data-dir examples/canonical` and no `--candidates-per-query` flag. `summarize-data --data-dir <dir>` prints a quick inventory of any data directory.

## Submission format

A submission is a single tab-separated file, with a header, covering all three task pairs in the canonical order (NCIT-DOID, SNOMED-FMA, SNOMED-NCIT). It has exactly four columns:

```text
SrcEntity    TgtEntity    Relation    Score
```

- `SrcEntity` — the query's source entity, from the candidates file.
- `TgtEntity` — one of that query's candidate targets.
- `Relation` — one of `equivalent`, `source_subsumed_by_target`, `source_subsumes_target`.
- `Score` — a finite float; higher ranks earlier.

The `verify` command enforces the same rules the platform submission will apply (see: [block_scoring.md](documentation/block_scoring.md)).

### Local scores are not leaderboard scores

> The local `score` always reports the kit-only `diagnostic_*` family — relation-aware binary relevance (MRR, Hits@K, MAP, top-1 relation macro-F1) — and adds the headline families when the sidecar files are present. The leaderboard reports the **Macro Preferred Relation-Aware (Typed) MRR** and **Macro Hierarchy-Aware Typed nDCG@10**, under the `preferred_typed_*` and `hierarchy_aware_typed_*` keys. The headline keys are computed by the same code paths the platform runs, so any divergence is the public-valid-vs-private-test split, not the implementation.

### Headline metric files

The headline families need two sidecar files next to the answers:

- `*.preferred.tsv` — the single preferred `(target, relation)` gold per query (`SrcEntity QueryID TgtEntity Relation`); drives Preferred Typed MRR and Hits@K.
- `*.graded.tsv` — graded-relevance gains over `(candidate, relation)` pairs (`SrcEntity QueryID TgtEntity Relation Gain`); drives Hierarchy-Aware Typed nDCG@10.

The public artifact ships both for the train and valid splits (the test versions stay server-side). `score` auto-discovers them next to the answers file by name (`NCIT-DOID.valid.answers.tsv` $\rightarrow$ `...preferred.tsv`, `...graded.tsv`); override with `--preferred` / `--graded`. You can rebuild the graded file yourself — deterministic given the preferred file and the `subclass_of` hierarchy:

```bash
PYTHONPATH=src python3 -m biokg_align_kit build-graded-relevance \
  --preferred  answers/NCIT-DOID.valid.preferred.tsv \
  --candidates tasks/NCIT-DOID/valid.cands.tsv \
  --triples    graph/triples.csv \
  --output     answers/NCIT-DOID.valid.graded.tsv
```

## Website

The competition website is served via GitHub Pages from the `docs/` folder.
