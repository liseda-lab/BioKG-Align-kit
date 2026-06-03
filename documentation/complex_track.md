# The complex track

BioKG-Align runs two competition tracks: the main track and the complex track. The former is a typed (relation-aware) candidate-ranking task, whereas the latter formalises alignment as a structured OWL-class expression generation problem. The two tracks involve different ontologies and evaluation procedures. We briefly introduce the complex track here (as originally described in the [whitepaper](https://biokg-align.lasige.di.ciencias.ulisboa.pt/whitepaper.pdf)).

## Task definition

Let $\mathcal{O}_B = \{\mathrm{BFO}, \mathrm{RO}, \mathrm{ChEBI}, \mathrm{CL}, \mathrm{GO}, \mathrm{PATO}, \mathrm{UBERON}, \mathrm{WBbt}\}$ be the background ontology set. For a source phenotype concept $c$ drawn from one of the three source ontologies $O_s \in \{\mathrm{HP}, \mathrm{MP}, \mathrm{WBP}\}$, produce an OWL 2 $\mathcal{EL}$ class expression $\hat{\phi}$, built from $\mathcal{O}_B$, that is a correct logical definition of $c$ — that is, $c \equiv \hat{\phi}$.

The expression is drawn from the OWL 2 $\mathcal{EL}$ fragment:

$$
\phi ::= A \;\mid\; \phi_1 \sqcap \phi_2 \;\mid\; \exists r.\phi,
$$

where $A \in \bigcup_{O \in \mathcal{O}_B} N_C(O)$ is a named class from a background ontology and $r \in N_R$ is an object property from the BFO or RO vocabularies. The expected output for each concept is an `owl:equivalentClass` axiom serialised in OWL/RDF/XML. This is a joint retrieval and generation problem: a correct answer requires both identifying the relevant classes across the background ontologies and assembling them into a logically coherent expression.

## Ontologies

Eleven OBO Foundry ontologies are involved. Eight supply the background knowledge (i.e., class and property vocabulary) that logical definitions are built from. Specifically, Basic Formal Ontology (BFO) and Relation Ontology (RO) provide upper-level and object property vocabularies (`part_of`, `has_part`, `inheres_in`, `has_quality`, `has_modifier`, related roles); and:

* Chemical Entities of Biological Interest (ChEBI)
* Cell Ontology (CL)
* Gene Ontology (GO)
* Phenotype and Trait Ontology (PATO)
* UBERON
* and WormBase Anatomy Ontology (WBbt)

contribute a range of relevant named classes. The remaining three are the source ontologies whose logical definitions the system must reconstruct. Each ships those definitions as `owl:equivalentClass` axioms expressed over the background ontologies; these serve as the reference alignment answers:

| Source ontology | Reference definitions |
|-----------------|----------------------:|
| HP — Human Phenotype Ontology | `5,554` |
| MP — Mammalian Phenotype Ontology | `9,312` |
| WBP — Worm Phenotype Ontology | `770` |
| **Total** | `15,636` |

The three source tasks differ substantially in scale and in the granularity of their phenotype descriptions.

## Worked example

The HP concept *decreased circulating cortisol level* (`HP:0008163`) has the reference logical definition:

$$
\begin{aligned}
\textsf{HP:0008163} \equiv {}
  & \exists\,\textsf{has\_part}.\big(\textsf{decreased\_amount} \\
  & \phantom{\exists\,\textsf{has\_part}.\big(}\sqcap\exists\,\textsf{inheres\_in}.(\textsf{cortisol}\sqcap\exists\,\textsf{part\_of}.\textsf{blood}) \\
  & \phantom{\exists\,\textsf{has\_part}.\big(}\sqcap\exists\,\textsf{has\_modifier}.\textsf{abnormal}\big).
\end{aligned}
$$

Note that every term is drawn from a background ontology:

- `decreased_amount`, `abnormal` — PATO (phenotypic qualities)
- `cortisol` — ChEBI (chemical entity)
- `blood` — UBERON (anatomy)
- `has_part`, `inheres_in`, `part_of`, `has_modifier` — BFO / RO (object properties)

A system that produces this expression for `HP:0008163` has reconstructed the concept's logical definition through (presumably) a mixture of both retrieval and generative processes over the set of background ontologies.

## Evaluation

The complex track is scored by graph edit distance-based (GED) f-measure, following Silva et al. (2025).

Each expression $\phi$ is converted to a directed labelled graph $\mathcal{G}(\phi)$ whose nodes are named classes and existential restrictions and whose edges encode the OWL constructor hierarchy. Given a generated expression $\hat{\phi}$ and the reference expression $\phi^\ast$ for a source concept $c$, the $\mathrm{GED}\big(\mathcal{G}(\hat{\phi}), \mathcal{G}(\phi^\ast)\big)$ measures the minimum-cost sequence of unit-cost node and edge edits (substitution, insertion, deletion) computed with NetworkX.

On the basis that each generated mapping is assigned one of six outcome categories:

| Category | Meaning |
|----------|---------|
| `correct` | the generated graph structurally matches the reference |
| `contains reference` | the reference graph is a subgraph of the generated graph |
| `contained in reference` | the generated graph is a subgraph of the reference |
| `incorrect` | neither graph subsumes the other |
| `false positive` | no reference exists for the concept |
| `false negative` | a reference exists but no expression was generated |

These categories map to Precision, Recall and F-measure following the category-to-outcome rules in Silva et al. (2025). The primary leaderboard metric is the macro-averaged $F_1$ across the three source ontologies:

$$
F_1^{\mathrm{macro}} = \tfrac{1}{3}\big(F_1^{\mathrm{HP}} + F_1^{\mathrm{MP}} + F_1^{\mathrm{WBP}}\big).
$$

The complex-track leaderboard is independent of the main-track leaderboard.

## Data, submission, and status

The complex track is the smaller of the two tracks and is described here at the proposal stage. 

The [whitepaper](https://biokg-align.lasige.di.ciencias.ulisboa.pt/whitepaper.pdf) fixes the aforementioned task details.

**Further information will be available soon.**
