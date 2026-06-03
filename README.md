# BioKG-Align Organizer Repository

This repository is the private organizer workspace for BioKG-Align. It contains the dataset-generation
pipeline, release validation code, benchmark orchestration, and documentation for how the official
challenge data is constructed.

Participants should not need to run this repository. The public starting kit lives in the submodule
at `public/BioKG-Align-kit` and is the place for the website, participant README, scorer, validator,
toy example, and simple baselines.

## What this repository owns

The private pipeline is responsible for:

- downloading or locking ontology source releases;
- normalizing OWL, RF2, OMIM-style, and prepared source files into a common interface;
- projecting ontology structure into public graph triples;
- projecting the documented Datalog-compatible OWL fragment into `rules.dl` and `facts.dl`;
- constructing equivalence and subsumption reference alignments;
- generating public train/validation labels and hidden test answers;
- building fixed candidate sets for each task;
- auditing leakage before a release is published;
- running organizer benchmarks.

The current synthetic fixture is still useful because it exercises the same file formats without
requiring licensed biomedical sources.

## Current status

The synthetic fixture runs end to end. The real-data pipeline now has the private structure for
hybrid acquisition, normalization, projection, Datalog output, and staged CLI commands. Source-specific
real adapters still need to be validated on the exact ontology releases selected for the challenge.

The projection protocol is documented in:

- `docs/data_generation_protocol.md`
- `docs/ontology_projection.md`
- `docs/reference_alignment_protocol.md`
- `docs/benchmark_protocol.md`

## Prerequisites

### Java

Required for reasoning with ELK (via mOWL).

_(on Ubuntu)_

```sh
sudo apt update
sudo apt install -y openjdk-17-jdk
# Add to ~/.bashrc (or ~/.zshrc)
export JAVA_HOME="$(dirname $(dirname $(readlink -f $(which java))))"
export PATH="$JAVA_HOME/bin:$PATH"
```

## Poetry setup

This repository is a Poetry project, but a fresh clone is not installed just because `pyproject.toml`
is present. Install the local package once before using the `biokg-align` command.

If Poetry itself is missing, install it using the official instructions:

```text
https://python-poetry.org/docs/#installation
```

From the repository root:

```bash
poetry --version
poetry install
poetry run biokg-align --help
```

For real-data work, install the optional dependencies as well:

```bash
poetry install --extras real
```

After that, prefer `poetry run biokg-align ...` for organizer commands. For quick debugging before
installing the package, `PYTHONPATH=src python3 -m biokg_align ...` still works, but it should not be
the normal workflow.

## Quick checks

Run the fixture pipeline:

```bash
poetry run biokg-align pipeline --config configs/fixture.json
```

Validate the generated fixture release:

```bash
poetry run biokg-align validate --config configs/fixture.json
```

Run tests:

```bash
poetry run python -m unittest
```

## Real-data stages

The real pipeline is staged so each step can be inspected:

```bash
poetry run biokg-align download --config configs/real.private.json
poetry run biokg-align normalize --config configs/real.private.json
poetry run biokg-align project --config configs/real.private.json
poetry run biokg-align build-release --config configs/real.private.json
poetry run biokg-align validate-release --config configs/real.private.json
poetry run biokg-align run-benchmarks --config configs/real.private.json
```

To run the pipeline end-to-end:

```bash
poetry run biokg-align pipeline --config configs/canonical.private.json
```

For production, do not use `configs/real_template.json` directly. Copy it to a private config, pin
all source versions, add checksums, and point restricted sources such as SNOMED CT and OMIM at
licensed local files or approved exports.

## Public kit submodule

Initialize the public kit after cloning:

```bash
git submodule update --init --recursive
```

Run its tests:

```bash
cd public/BioKG-Align-kit
PYTHONPATH=src python3 -m unittest
```

The public kit should remain free of hidden labels, real-data adapters, licensed paths, private split
logic, and organizer-only benchmark outputs.

## Pushing changes

The public kit is a Git submodule, so its commit must be pushed before the private repository records
that submodule pointer.

Push the public kit first:

```bash
cd public/BioKG-Align-kit
git status
git push -u origin main
```

Then push this private organizer repository:

```bash
cd ../..
git status
git submodule status
git push -u origin main
```

When the public kit changes later, commit and push inside the submodule first:

```bash
cd public/BioKG-Align-kit
git status
git add README.md docs src examples pyproject.toml
git commit -m "Update public starting kit"
git push
```

Then return to this repository and commit the updated submodule pointer:

```bash
cd ../..
git status
git add public/BioKG-Align-kit
git commit -m "Update public kit submodule"
git push
```

If the submodule commit is not pushed first, GitHub will show the private repository pointing at a
public-kit revision that other people cannot fetch.
