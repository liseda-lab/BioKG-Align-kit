# BioKG-Align Kit

This is the public starting kit for BioKG-Align, a biomedical knowledge graph alignment challenge. It is the part participants should use. It does not generate the official dataset and it does not contain hidden test labels.

The main track asks a single question over a unified biomedical graph: given a source entity and a fixed list of candidate targets from another ontology, **which candidate is correct, and what relation does it hold to the source?** The relations are `equivalent`, `source_subsumed_by_target`, and `source_subsumes_target`. For every query a system submits scored candidate–relation pairs, and a prediction counts only when **both** the target entity and the relation are correct.

**Track scope.** This kit covers the **main track** (typed candidate ranking); the competition's **complex track** (OWL class-expression generation) is detailed under [documentation/complex_track.md](documentation/complex_track.md).

## What's in the kit

- the scorer of the challenge (the platform runs the same code): Preferred Typed MRR (primary),
  Hierarchy-Aware Typed nDCG@10 (secondary) and the diagnostic family
- `verify`: the platform's strict submission rules, locally
- the simple `random` and `hybrid_lexical` baselines
- three example fixtures in the release layout:
  - `mini` — compact, human-readable (4 candidates per query)
  - `canonical` — 50 candidates per query, production shape
  - `mini_paired` — two sources, each with an equivalence-mode and a subsumption-mode query (different pools)
- a Datalog reader plus optional Soufflé-backed OWL 2 RL conflict scoring for released graph programs
- a `build-graded-relevance` helper for the Hierarchy-Aware gain table
- specifications under [documentation/](documentation/README.md)

The [official dataset](https://biokg-align.lasige.di.ciencias.ulisboa.pt/data/) is distributed separately as a public data artifact. Download it, train your methods, and use this kit to score them locally and to validate submissions.

## Install

```bash
python3 -m pip install -e .
```

Or run without installing:

```bash
PYTHONPATH=src python3 -m biokg_align_kit --help
```

The kit has no runtime dependencies beyond the Python standard library (Python ≥ 3.10).

## Quickstart

Generate the `hybrid_lexical` baseline on the bundled `mini` fixture, validate it with the platform rules, then score it:

```bash
PYTHONPATH=src python3 -m biokg_align_kit run-baseline \
  --data-dir examples/mini --task NCIT-DOID --split valid \
  --baseline hybrid_lexical --output /tmp/mini.tsv

PYTHONPATH=src python3 -m biokg_align_kit verify \
  --predictions /tmp/mini.tsv --data-dir examples/mini --split valid

PYTHONPATH=src python3 -m biokg_align_kit score \
  --predictions /tmp/mini.tsv --data-dir examples/mini --split valid
```

The same three commands work with `--data-dir examples/canonical` (50 candidates per query) and on the released data. `summarize-data --data-dir <dir>` prints a quick inventory of any data directory.

## Submission format

A submission is a single tab-separated file, with a header, covering every task of the phase. It has exactly five columns and is joined to the queries by `QueryID` (row order does not matter):

```text
QueryID    SrcEntity    TgtEntity    Relation    Score
```

- `QueryID` — the query's opaque identifier from `tasks/<task>/<split>.cands.tsv` (`<task>-<8 hex>`).
- `SrcEntity` — the query's source entity, from the same row.
- `TgtEntity` — one of that query's candidate targets.
- `Relation` — one of `equivalent`, `source_subsumed_by_target`, `source_subsumes_target`.
- `Score` — a finite float; higher ranks earlier.

Every query needs exactly one row per (candidate, relation) pair — 150 rows per query. `verify` applies the scoring platform's rules, meaning any of the following fatal errors will result in a rejected submission (i.e., at submission time; _see below for lenient and script local scoring_): unknown or missing queries, missing, extra or duplicate pairs, off-pool targets, bad relations, non-finite scores and `SrcEntity` mismatches (see [submission_format.md](documentation/submission_format.md) and [submission_scoring.md](documentation/submission_scoring.md)).

### Lenient (and verifiable) local scoring

Executing `score` on train/valid splits runs the same checks as when verifying the submission for platform scoring. However, the local scorer is in _lenient mode_ by default, where it reports violations as **warnings** with a documented fallback (e.g., invalid rows dropped, duplicates max-merged, missing pairs of a present query, absent queries skipped). These local scored values are, of course, not comparable to reported leaderboard metrics since they do not include the private test set. 

Use `score --strict` (or `verify`) to suppress the above-mentioned warnings. Local scores are computed by exactly the same codes that the scoring platform runs. Note that differences from the leaderboard are then entirely based on the public/private splits. That is, public train/valid splits only run locally, whereas the private test split is run on the scoring platform (CodaBench).

### Evaluation files

The release ships, for train and valid splits only. These are available at the following directories: `evaluation/<task>/<split>.answers.tsv` (gold and pool), `<split>.preferred.tsv` (the single preferred `(target, relation)` per query; drives the MRR family), `<split>.graded.tsv` (graded gains; drives H-nDCG@10) and `evaluation/query_metadata.tsv` (task, split, source and `QueryMode` per query). The test versions stay with the organisers. You can rebuild a graded file yourself as shown below:

```bash
PYTHONPATH=src python3 -m biokg_align_kit build-graded-relevance \
  --preferred  evaluation/NCIT-DOID/valid.preferred.tsv \
  --candidates tasks/NCIT-DOID/valid.cands.tsv \
  --triples    graph/triples.csv \
  --output     /tmp/NCIT-DOID.valid.graded.tsv
```

## Website

The competition website is served via GitHub Pages from the `docs/` folder.
