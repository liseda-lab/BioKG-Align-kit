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

## What is in this kit

- a local scorer for validation answers;
- a submission validator;
- simple random and lexical baselines;
- a tiny example dataset;
- the challenge website draft under `docs/`.

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

## Try the example

Score the example lexical baseline against the tiny answer file:

```bash
PYTHONPATH=src python3 -m biokg_align_kit run-baseline \
  --data-dir examples/mini \
  --task NCIT-DOID \
  --split valid \
  --baseline lexical \
  --output /tmp/biokg-align-mini.tsv

PYTHONPATH=src python3 -m biokg_align_kit score \
  --predictions /tmp/biokg-align-mini.tsv \
  --answers examples/mini/answers/NCIT-DOID.valid.answers.tsv
```

Validate a submission against candidate files:

```bash
PYTHONPATH=src python3 -m biokg_align_kit validate-submission \
  --predictions /tmp/biokg-align-mini.tsv \
  --candidates examples/mini/tasks/NCIT-DOID/valid.cands.tsv
```

Summarize a data directory:

```bash
PYTHONPATH=src python3 -m biokg_align_kit summarize-data --data-dir examples/mini
```

## Submission format

Predictions are TSV files with four columns:

```text
SrcEntity    TgtEntity    Relation    Score
```

`Score` is a numeric value. Higher scores rank earlier. You may submit multiple rows for the same
source entity, one for each candidate-relation pair you want scored.

## Website

The website draft is in `docs/` and can be served by GitHub Pages from the `docs` folder.
