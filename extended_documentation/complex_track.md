# Complex track (out of kit scope)

The BioKG-Align competition has two tracks:

- **Main track** — typed candidate ranking. Each query has a fixed
  list of 50 candidate target entities and the task is to rank
  `(candidate, relation)` pairs. **The kit covers the main track in
  full.**
- **Complex track** — OWL class expression generation. Each query
  asks the system to generate a class expression in the partner
  ontology that aligns with the source entity. The output is
  structurally and evaluatively distinct from a ranked candidate
  list.

The complex track has its own data release, its own submission
format, and its own evaluation pipeline. None of the kit's
machinery — scorer, validator, baselines, fixtures, Datalog reader —
applies to the complex track submission flow.

If you're interested in participating in the complex track, refer
to the competition's main website (`docs/` in this repository
serves the website draft) and the complex-track-specific
announcement on the competition page. The complex track materials
are released as a separate artifact.

## Why this matters for kit users

When the kit's documentation mentions "the submission" it
exclusively means the main-track block-format TSV — never a
complex-track artifact. If you find yourself looking at a complex-
track question (class expression syntax, expression-level metrics,
the complex test set) the answer is not in this kit; consult the
complex-track materials instead.
