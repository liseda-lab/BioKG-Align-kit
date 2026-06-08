# Data Card — BioKG-Align v0.2.0

This data card describes the public artifacts released for the BioKG-Align competition (NeurIPS 2026 Competition Track). It includes concrete file-level documentation aimed at participants integrating the data into their pipelines. For the other kit-side reference documents see the rest of this directory.

## Overview

BioKG-Align is a typed link-prediction benchmark over a unified biomedical knowledge graph projected from five OWL ontologies: SNOMED CT, NCIT, FMA, ORDO, and DOID. The competition graph contains 824,035 nodes and 2,781,910 triples; upstream cross-reference resources are used to construct the reference alignments but are not released as task ontologies in the graph.

The main track defines three task pairs: NCIT-DOID, SNOMED-FMA, and SNOMED-NCIT. The release contains 30,216 test queries across 15,550 source entities. Each query has 50 candidate target entities and three relation types, producing a 150-row submission block per query.

## Released artifacts

Top-level release layout:

```
public/
├── graph/
│   ├── triples.csv
│   ├── properties.csv
│   ├── facts.dl
│   ├── rules.dl
│   ├── datalog_terms.tsv
├── alignments/
│   ├── train.tsv
│   └── valid.tsv
└── tasks/
    ├── NCIT-DOID/
    │   ├── train.cands.tsv
    │   ├── valid.cands.tsv
    │   ├── test.cands.tsv
    │   ├── train.preferred.tsv
    │   ├── valid.preferred.tsv
    │   ├── train.graded.tsv
    │   └── valid.graded.tsv
    ├── SNOMED-FMA/  (same layout)
    └── SNOMED-NCIT/ (same layout)
```

The corresponding test-split files (`<task>.test.answers.tsv`, `<task>.test.preferred.tsv`, `<task>.test.graded.tsv`) are organiser-only and not distributed; the scorer runs server-side against them after a submission is uploaded.

## File schemas

### `graph/triples.csv`

Typed graph triples including intra-ontology relations and released training anchors.

| Column              | Type    | Description                                                                                          |
|---------------------|---------|------------------------------------------------------------------------------------------------------|
| `triple_id`         | string  | Stable per-triple identifier (e.g. `T00000001`).                                                     |
| `head_id`           | string  | Source (head) entity identifier (e.g. `SNOMED:12345`).                                               |
| `relation`          | string  | Relation type: hierarchy (subclass) edges plus ontology object properties (262 distinct values).     |
| `tail_id`           | string  | Target (tail) entity identifier.                                                                     |
| `head_ontology`     | string  | Head entity's ontology code.                                                                         |
| `tail_ontology`     | string  | Tail entity's ontology code.                                                                         |
| `source`            | string  | Edge origin: `ontology` (asserted axiom), `ontology_projection` (mOWL OWL2Vec*), or `verified_lexical_anchor`. |
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

### `graph/facts.dl`, `graph/rules.dl`, `graph/datalog_terms.tsv`

Canonical Soufflé-compatible OWL 2 RL/RDF files. `facts.dl` contains asserted logical RDF triples as `source_triple(graph,s,p,o)` plus literal support facts. `rules.dl` derives the OWL 2 RL closure as `triple(s,p,o)` and reports contradictions as `inconsistency(...)`. `datalog_terms.tsv` maps stable symbols back to IRIs, literals, datatypes, language tags, and blank nodes. The previous class-centric projection is retained for compatibility as `legacy_projection_facts.dl` and `legacy_projection_rules.dl`.

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

## Submission format

The participant submission is a single TSV across all three task pairs:

```
SrcEntity   TgtEntity   Relation   Score
```

Submission rows are grouped positionally into per-query blocks of size 150 (`candidate_count x |submission.relations|`, 50 $\times$ 3 = 150). Block `k` corresponds to the k-th row of the concatenated `test.cands.tsv` files (canonical task order: NCIT-DOID, SNOMED-FMA, SNOMED-NCIT — see `../submission_schema.json`).

For the canonical build (30,216 test queries), a full submission is `30,216 x 150 = 4,532,400` rows.

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

Licence terms for SNOMED CT and FMA complicate direct redistribution of the source ontologies; organisers intend to provide download / reconstruction scripts in the starting kit (`biokg-align download`) that recover the source files from upstream under the participant's own licence.

## Splits

Splits are by source query within each task pair. The same source entity does not appear in both training and hidden test with different targets. Relation balance across train, valid, and test is preserved where the task size allows; the split report records imbalances explicitly.

## Ethics and intended use

The released data are curated biomedical ontologies. They contain no human-subject data, patient records, or PII. They describe biomedical concepts (diseases, anatomical structures, procedures, drugs, phenotypes) at the ontology level. Models trained on this benchmark are research prototypes and must not be used for clinical decision-making without independent expert validation.

Known limitations:

- **English-only labels.** Released labels and definitions are English-language. Cross-lingual alignment is out of scope.
- **Snapshot in time.** The ontology versions are the ones listed in the proposal (SNOMED CT 2026-03-01 US edition, etc.). Live ontology updates are not reflected.
- **Hidden-test leakage defences are best-effort.** Cross-reference annotations that would directly expose hidden labels are withheld from `properties.csv`, but participants with access to upstream cross-reference resources may be able to reconstruct gold targets through external resources. Competition rules prohibit such reconstruction.

## Versioning

This data card describes v0.2.0 _(rc3, at this time)_. v0.1.0 and v0.1.1 were internal development builds; v0.1.2 introduced subsumption-only sampling; v0.1.3 moved to the pool model and the 4-column block-scoring submission format; v0.2.0 is intended for NeurIPS 2026 release.
