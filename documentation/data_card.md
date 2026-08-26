# Data Card — BioKG-Align v0.3.0

This data card describes the public artifacts released for the BioKG-Align competition (AAAI 2027). It includes concrete file-level documentation aimed at participants integrating the data into their pipelines. For the other kit-side reference documents see the rest of this directory.

## Overview

BioKG-Align is a typed link-prediction benchmark over a unified biomedical knowledge graph projected from five OWL ontologies: SNOMED CT, NCIT, FMA, ORDO, and DOID. The competition graph contains 723,938 nodes and 2,180,708 triples; upstream cross-reference resources are used to construct the reference alignments but are not released as task ontologies in the graph.

The main track defines three task pairs: NCIT-DOID, SNOMED-FMA, and SNOMED-NCIT. The release contains 29,490 test queries across 15,168 source entities. Each query has 50 candidate target entities and three relation types, producing a 150-row submission block per query.

## Released artifacts

Top-level release layout:

```
public/
├── graph/
│   ├── triples.csv
│   ├── properties.csv
│   ├── anchors_train.tsv
│   ├── facts.dl                  # .input driver
│   ├── <relation>.facts          # one TSV per fact relation (see datalog_schema.json)
│   ├── owl2rl_core.dl
│   ├── rules.dl
│   ├── conflict_rules.dl
│   ├── legacy_projection_rules.dl
│   ├── legacy_projection_facts.dl
│   ├── datalog_terms.tsv
│   ├── datalog_schema.json
│   ├── node_schema.json
│   └── relation_schema.json
├── alignments/
│   ├── train.tsv
│   └── valid.tsv
├── tasks/
│   ├── NCIT-DOID/
│   │   ├── train.cands.tsv
│   │   ├── valid.cands.tsv
│   │   ├── test.cands.tsv
│   │   ├── train.preferred.tsv
│   │   ├── valid.preferred.tsv
│   │   ├── train.graded.tsv
│   │   ├── valid.graded.tsv
│   │   ├── train.composition.json
│   │   └── valid.composition.json
│   ├── SNOMED-FMA/  (same layout)
│   └── SNOMED-NCIT/ (same layout)
├── baseline_predictions/
├── evaluation/                    # sample_submission.tsv, submission_schema.json, scorer
├── baseline_results.json / baseline_results_macro.json / baseline_results.md
├── README.md / data_card.md       # in-package pointers to this documentation
├── release_manifest.json
└── license_manifest.json
```

The corresponding test-split files (`<task>.test.answers.tsv`, `<task>.test.preferred.tsv`, `<task>.test.graded.tsv`) are organiser-only and not distributed; the scorer runs server-side against them after a submission is uploaded.

## File schemas

### `graph/triples.csv`

Typed graph triples including intra-ontology relations and released training anchors.

| Column              | Type    | Description                                                                                          |
|---------------------|---------|------------------------------------------------------------------------------------------------------|
| `triple_id`         | string  | Stable per-triple identifier (e.g. `T00000001`).                                                     |
| `head_id`           | string  | Source (head) entity identifier (e.g. `SNOMED:12345`).                                               |
| `relation`          | string  | Relation type: hierarchy (subclass) edges plus ontology object properties (348 distinct values).     |
| `tail_id`           | string  | Target (tail) entity identifier.                                                                     |
| `head_ontology`     | string  | Head entity's ontology code.                                                                         |
| `tail_ontology`     | string  | Tail entity's ontology code.                                                                         |
| `source`            | string  | Edge origin: `ontology` (asserted axiom), `ontology_hierarchy` (normalized hierarchy row, e.g. RF2 is-a), `ontology_projection` (mOWL OWL2Vec*), or `verified_lexical_anchor`. |
| `provenance`        | string  | Free-text origin detail (e.g. `source_axiom`, the source ontology code, or the anchor method).       |
| `is_inferred`       | boolean | Reasoner-closure flag.                                                                               |
| `is_anchor`         | boolean | `true` for the cross-ontology training anchors (10 in the canonical build), `false` otherwise.       |
| `source_axiom_type` | string  | Originating axiom/projection class: `SubClassOf`, `OWL2VecStar`, `ExistentialRestriction`, or `AlignmentAnchor`. |
| `projection_method` | string  | How the edge was produced (e.g. `normalized_subclass`, `mowl_owl2vecstar`, `normalized_existential`). |
| `release_layer`     | string  | Release layer; `public` for every edge in the public package.                                        |

### `graph/properties.csv`

Per-entity metadata.

| Column                | Type   | Description                                                                       |
|-----------------------|--------|-----------------------------------------------------------------------------------|
| `node_id`             | string | Entity identifier in `ONTOLOGY:LOCAL_ID` form (e.g. `DOID:DOID_0001816`).         |
| `ontology`            | string | Source ontology code.                                                             |
| `iri`                 | string | Full OWL IRI.                                                                     |
| `local_id`            | string | Ontology-local identifier (`node_id` without the ontology prefix).                |
| `preferred_label`     | string | Primary label.                                                                    |
| `synonyms`            | string | Alternative labels; multiple synonyms are `\|`-separated. Empty where none.       |
| `definition`          | string | Definition text where present (populated for a minority of entities).             |
| `semantic_category`   | string | High-level semantic type; not populated in the public release.                    |
| `source_version`      | string | Version of the source ontology the entity was drawn from.                         |
| `property_provenance` | string | Provenance tag for the released properties (`preprocessed_public_safe`).          |

Note: annotations that could expose hidden test labels are withheld from this file.

### OWL 2 RL Datalog files

Canonical Soufflé-compatible OWL 2 RL/RDF files. `facts.dl` is the fact-loading driver: the asserted facts ship as one tab-separated `<relation>.facts` file per relation (`source_triple` carries the logical RDF triples as `(graph, s, p, o)` rows; the remaining relations are literal support facts), loaded via Soufflé `.input` directives — inline atoms would be unparseable at release scale. Run Soufflé from the `graph/` directory (or pass `-F graph/`). `owl2rl_core.dl` contains declarations and recursive rules. `rules.dl` outputs the full closure and inconsistencies, while `conflict_rules.dl` is the lightweight scoring driver that outputs only `inconsistency(...)`. `datalog_terms.tsv` maps stable symbols back to IRIs, literals, datatypes, language tags, and blank nodes. The previous class-centric projection is retained for compatibility as `legacy_projection_facts.dl` and `legacy_projection_rules.dl`.

When the scorer is given `--graph-dir`, it inserts the submission's top mapping per query as temporary RDF facts and reports only inconsistencies that were not already present in the baseline public graph. This is an OWL 2 RL Datalog inconsistency metric, not a full-OWL unsatisfiable-class count. The same program can drive a coherence check via canary individuals (`building_with_kit.md`, Workflow 8).

**Withheld axiom families (declared post-processing, v0.3.0).** The released fact base is curated so that the shipped theory cannot contradict the reference alignment: pairwise disjointness axioms inconsistent with the complete reference alignment were removed (105 of 26,553), and list-based disjointness (`owl:AllDisjointClasses`) is withheld entirely, as are `owl:FunctionalProperty` / `owl:InverseFunctionalProperty` typings. The exact aggregate counts ship machine-readably in `datalog_schema.json` (`withheld_axiom_families`, `disjointness_curation`); the build verifies that canary probing of the released facts — alone, or with any subset of the reference alignment merged — yields **zero** inconsistency witnesses. Two consequences participants can rely on:

- The retained `owl:disjointWith` axioms are **certified consistent with every reference mapping**: if merging one of your candidate mappings induces an inconsistency, that mapping is not in the reference. Conflict counts (Workflow 5) and canary checks (Workflow 8) are therefore sound pruning signals.
- A clean check is expected, not evidence of full-OWL coherence: the curation removes real upstream axioms (the evidence-based reference is not DL-coherent against them), axioms with existential superclasses (`C ⊑ ∃R.D`) remain outside OWL 2 RL and excluded from the facts (visible quantifier-free as projected edges in `triples.csv`), and full-ontology reasoning requires the upstream OWL sources with a DL reasoner.

### `alignments/train.tsv`, `alignments/valid.tsv`

Cross-ontology mappings with relation types, used as training and validation labels.

| Column                    | Type   | Description                                                                  |
|---------------------------|--------|------------------------------------------------------------------------------|
| `source_id`               | string | Source entity.                                                               |
| `target_id`               | string | Target entity.                                                               |
| `source_ontology`         | string | Source entity's ontology code.                                               |
| `target_ontology`         | string | Target entity's ontology code.                                               |
| `relation`                | string | One of `equivalent`, `source_subsumed_by_target`, `source_subsumes_target`.  |
| `split`                   | string | `train` or `valid`.                                                          |
| `provenance`              | string | Construction-provenance metadata.                                            |
| `confidence_category`     | string | Reference-confidence tag (e.g. `derived_primary`).                           |
| `generation_method`       | string | How the reference pair was generated.                                        |
| `source_ontology_version` | string | Version of the source ontology.                                              |
| `target_ontology_version` | string | Version of the target ontology.                                              |

### `tasks/<task>/{train,valid}.cands.tsv`

Per-query candidate sets with gold labels (public on train/valid).

| Column         | Type   | Description                                                  |
|----------------|--------|--------------------------------------------------------------|
| `SrcEntity`    | string | Source query entity.                                         |
| `QueryID`      | string | Per-source query identifier (`Q0` is pref. $\equiv$, `Q1` is pref. $\in \{\ \sqsubseteq,\ \sqsupseteq\ \}$). |
| `TgtEntities`  | list   | Primary gold target as a bracketed list — a single element under the N=1 gold model. |
| `Relations`    | list   | Gold relation parallel to `TgtEntities` (single element).    |
| `TgtCandidates`| list   | Fixed set of 50 candidate target entities (bracketed list).  |

### `tasks/<task>/test.cands.tsv`

| Column         | Type   | Description                                |
|----------------|--------|--------------------------------------------|
| `SrcEntity`    | string | Source query entity.                       |
| `TgtCandidates`| list   | Fixed set of 50 candidate target entities. |

No `QueryID` column; no gold leak. Block scoring recovers `QueryID` positionally.

### `tasks/<task>/{train,valid}.preferred.tsv`

Per-query preferred gold pair (two-file evaluation design, public on train/valid).

| Column      | Type   | Description                              |
|-------------|--------|------------------------------------------|
| `SrcEntity` | string | Source query entity.                     |
| `QueryID`   | string | Per-source query identifier.             |
| `TgtEntity` | string | Preferred target entity.                 |
| `Relation`  | string | Preferred relation.                      |

### `tasks/<task>/{train,valid}.graded.tsv`

Per-query graded relevance file for the secondary metric (Hierarchy-Aware Typed nDCG@10).

| Column      | Type   | Description                                      |
|-------------|--------|--------------------------------------------------|
| `SrcEntity` | string | Source query entity.                             |
| `QueryID`   | string | Per-source query identifier.                     |
| `TgtEntity` | string | Candidate target.                                |
| `Relation`  | string | Relation.                                        |
| `Gain`      | float  | Graded gain in `[0, 1]`.                          |

### `tasks/<task>/{train,valid}.composition.json`

Per-query candidate-pool composition report: how many of each query's 50 candidates were contributed by each construction tier (`gold`, `lexical`, `neighbourhood`, `random`, `rerank`), per query and in aggregate. Diagnostic transparency only — not needed for training or scoring.

## Submission format

The participant submission is a single TSV across all three task pairs:

```
SrcEntity   TgtEntity   Relation   Score
```

Submission rows are grouped positionally into per-query blocks of size 150 (`candidate_count x |submission.relations|`, 50 $\times$ 3 = 150). Block `k` corresponds to the k-th row of the concatenated `test.cands.tsv` files (canonical task order: NCIT-DOID, SNOMED-FMA, SNOMED-NCIT — see `../submission_schema.json`).

For the canonical build (29,490 test queries), a full submission is `29,490 x 150 = 4,423,500` rows.

The kit's `verify` CLI command validates the submission locally before upload; see kit README for usage.

## Licences

| Resource     | Licence                                                      |
|--------------|--------------------------------------------------------------|
| SNOMED CT    | SNOMED CT Affiliate License (institutional access required). |
| NCIT         | CC BY 4.0.                                                   |
| FMA          | FMA License (terms apply to redistribution).                 |
| ORDO         | CC BY 4.0.                                                   |
| DOID         | CC0 1.0.                                                     |
| Code & docs  | See `../LICENSE`.                                            |

Licence terms for SNOMED CT and FMA complicate direct redistribution of the source ontologies; organisers intend to provide download / reconstruction scripts in the starting kit (a planned `biokg-align-kit download` subcommand; not yet shipped) that recover the source files from upstream under the participant's own licence.

## Splits

Splits are by source query within each task pair. The same source entity does not appear in both training and hidden test with different targets. Relation balance across train, valid, and test is preserved where the task size allows; the split report records imbalances explicitly.

## Ethics and intended use

The released data are curated biomedical ontologies. They contain no human-subject data, patient records, or PII. They describe biomedical concepts (diseases, anatomical structures, procedures, drugs, phenotypes) at the ontology level. Models trained on this benchmark are research prototypes and must not be used for clinical decision-making without independent expert validation.

Known limitations:

- **English-only labels.** Released labels and definitions are English-language. Cross-lingual alignment is out of scope.
- **Snapshot in time.** The ontology versions are the ones listed in the proposal (SNOMED CT 2026-03-01 US edition, etc.). Live ontology updates are not reflected.
- **Hidden-test leakage defences are best-effort.** Cross-reference annotations that would directly expose hidden labels are withheld from `properties.csv`, but participants with access to upstream cross-reference resources may be able to reconstruct gold targets through external resources. Competition rules prohibit such reconstruction.

## Versioning

This data card describes v0.3.0 _(rc1, at this time)_. v0.1.0 and v0.1.1 were internal development builds; v0.1.2 introduced subsumption-only sampling; v0.1.3 moved to the pool model and the 4-column block-scoring submission format; v0.2.0 (last built as rc3) froze the one-preferred-gold metric contract; v0.2.1 kept that contract, added the publication-readiness fixes (scoring, leakage, and reproducibility), and moved the Datalog facts to the `.input` layout; v0.3.0 withholds the class-clash axiom families from the released facts (the declared reference-consistency curation above) — it is the version intended for AAAI 2027 release.
