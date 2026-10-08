"""
Generate the canonical-shape fixture (``examples/canonical/``).

The canonical fixture has the same layout as the mini fixture (and as a
v0.4.0 release: ``tasks/<task>/<split>.cands.tsv`` with ``QueryID,
SrcEntity, TgtCandidates``; ``evaluation/<task>/<split>.{answers,
preferred,graded}.tsv``; ``evaluation/query_metadata.tsv``;
``release_manifest.json``) but uses ``|C_q| = 50`` candidates per query —
matching the official challenge shape. Unlike the mini fixture, it is generated rather than
hand-authored, because 50-row TSVs would be unreadable as hand-written
source.

What it exercises that the mini fixture doesn't:

- The full ``50 x 3 = 150`` candidate-relation pair count per query in
  submissions, matching paper §2.1. The mini fixture has 4 candidates
  per query and exists to be human-readable, not realistic.

Run::

    python3 examples/canonical/_build_canonical_fixture.py

The script is deterministic; re-running produces byte-identical output.
The committed canonical fixture is the output of running this script
once. If the script logic changes, regenerate and re-commit.

Design choices:

- Single task (``NCIT-DOID``) — adding more tasks would multiply the
  fixture size without exercising additional kit code paths.
- Single split (``valid``) — same rationale.
- 3 queries — enough to exercise per-query macro-averaging in the
  scorer; few enough to stay under ~1k total rows across all files.
- Depth-3 hierarchy under each gold target — exercises the
  hierarchy-aware nDCG@10 partial-credit gain formula at distances 1,
  2, and 3 (the default ``max_distance``).
- Equivalence-preferred queries only — keeps the preferred-pair file
  rule trivially deterministic.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

# Resolve fixture root relative to this script.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1] / "src"))

from biokg_align_kit.hierarchy import (  # noqa: E402
    compute_graded_relevance,
    load_hierarchy_from_triples,
    write_graded_relevance,
)

TASK = "NCIT-DOID"
GRAPH_DIR = HERE / "graph"
TASKS_DIR = HERE / "tasks" / TASK
EVALUATION_DIR = HERE / "evaluation"


# Three queries, each with a different source NCIT class.
QUERIES: list[tuple[str, str]] = [
    ("NCIT:C001", "DOID:D001"),  # Q1: gold pair (equivalent)
    ("NCIT:C002", "DOID:D002"),  # Q2: gold pair (equivalent)
    ("NCIT:C003", "DOID:D003"),  # Q3: gold pair (equivalent)
]


def query_id(split: str, source_id: str) -> str:
    """Deterministic opaque fixture ID in the release format <task>-<8 hex>."""
    digest = hashlib.sha256(f"kit-example:canonical:{split}:{source_id}".encode()).hexdigest()
    return f"{TASK}-{digest[:8]}"


def make_properties() -> str:
    """Build ``properties.csv``: NCIT sources + DOID candidates with hierarchy."""
    header = (
        "node_id,ontology,iri,local_id,preferred_label,synonyms,"
        "definition,semantic_category,source_version\n"
    )
    rows: list[str] = []

    # NCIT sources (one per query).
    for source_id, _ in QUERIES:
        local = source_id.split(":")[1]
        rows.append(
            f"{source_id},NCIT,https://example.org/NCIT/{local},{local},"
            f"Synthetic NCIT concept {local},,Synthetic source concept.,"
            f"disease,canonical\n"
        )

    # DOID gold targets and their hierarchical neighbours.
    # For each gold target DOID:DNNN we generate:
    #   - DOID:DNNN itself (gold, equivalent)
    #   - 1 parent at distance 1 (under a synthetic root)
    #   - 1 grandparent at distance 2
    #   - 1 great-grandparent at distance 3 (the root)
    #   - 3 children at distance 1
    #   - 6 grandchildren (2 per child) at distance 2
    # Plus padding distractors to reach 50 candidates per query.
    for _, gold_id in QUERIES:
        local = gold_id.split(":")[1]
        family = [
            (gold_id, f"DOID class {local} (gold)"),
            (f"DOID:P{local}", f"DOID parent of {local}"),
            (f"DOID:G{local}", f"DOID grandparent of {local}"),
            (f"DOID:R{local}", f"DOID root above {local}"),
        ]
        for i in range(1, 4):
            family.append((f"DOID:C{local}_{i}", f"DOID child {i} of {local}"))
            for j in range(1, 3):
                family.append((
                    f"DOID:C{local}_{i}_{j}",
                    f"DOID grandchild {j} of child {i} of {local}",
                ))
        # Padding distractors (no hierarchical relation to the gold).
        for k in range(1, 38):
            family.append((f"DOID:X{local}_{k:02d}", f"DOID distractor {k} for {local}"))

        for node_id, label in family:
            local_id = node_id.split(":")[1]
            rows.append(
                f"{node_id},DOID,https://example.org/DOID/{local_id},{local_id},"
                f"{label},,Synthetic DOID concept.,disease,canonical\n"
            )

    return header + "".join(rows)


def make_triples() -> str:
    """Build ``triples.csv`` with the hierarchy needed by graded relevance."""
    header = (
        "triple_id,head_id,relation,tail_id,head_ontology,tail_ontology,"
        "source,provenance,is_inferred,is_anchor,release_layer\n"
    )
    rows: list[str] = []
    triple_id = 1

    def emit(head: str, tail: str) -> None:
        nonlocal triple_id
        rows.append(
            f"T{triple_id:08d},{head},subclass_of,{tail},DOID,DOID,"
            f"canonical,asserted,false,false,public\n"
        )
        triple_id += 1

    for _, gold_id in QUERIES:
        local = gold_id.split(":")[1]
        # Vertical: gold -> parent -> grandparent -> root
        emit(gold_id, f"DOID:P{local}")
        emit(f"DOID:P{local}", f"DOID:G{local}")
        emit(f"DOID:G{local}", f"DOID:R{local}")
        # Children: 3 children of the gold; 2 grandchildren each
        for i in range(1, 4):
            emit(f"DOID:C{local}_{i}", gold_id)
            for j in range(1, 3):
                emit(f"DOID:C{local}_{i}_{j}", f"DOID:C{local}_{i}")

    return header + "".join(rows)


def make_pools() -> list[list[str]]:
    """Per-query 50-candidate pools, sorted as in the release."""
    pools: list[list[str]] = []
    for _source_id, gold_id in QUERIES:
        local = gold_id.split(":")[1]
        candidates: list[str] = [gold_id, f"DOID:P{local}", f"DOID:G{local}", f"DOID:R{local}"]
        for i in range(1, 4):
            candidates.append(f"DOID:C{local}_{i}")
            for j in range(1, 3):
                candidates.append(f"DOID:C{local}_{i}_{j}")
        for k in range(1, 38):
            candidates.append(f"DOID:X{local}_{k:02d}")
        assert len(candidates) == 50, f"Expected 50 candidates, got {len(candidates)}"
        pools.append(sorted(candidates))
    return pools


def _tsv(header: list[str], rows: list[list[str]]) -> str:
    return "\t".join(header) + "\n" + "".join("\t".join(row) + "\n" for row in rows)


def write_all() -> None:
    """Generate every fixture file and write it to disk."""
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    (EVALUATION_DIR / TASK).mkdir(parents=True, exist_ok=True)

    (GRAPH_DIR / "properties.csv").write_text(make_properties())
    (GRAPH_DIR / "triples.csv").write_text(make_triples())

    pools = make_pools()
    hierarchy = load_hierarchy_from_triples(GRAPH_DIR / "triples.csv")
    cands, answers, preferred, metadata, test = [], [], [], [], []
    gains: dict[str, dict[tuple[str, str], float]] = {}
    sources: dict[str, str] = {}
    for (source_id, gold_id), pool in zip(QUERIES, pools):
        qid = query_id("valid", source_id)
        pool_repr = repr(pool)
        cands.append([qid, source_id, pool_repr])
        answers.append([qid, source_id, repr([gold_id]), repr(["equivalent"]), pool_repr])
        preferred.append([qid, source_id, gold_id, "equivalent"])
        metadata.append([qid, TASK, "valid", source_id, "equivalence"])
        test.append([query_id("test", source_id), source_id, pool_repr])
        # Built with the kit's own rule, so the fixture is consistent with how
        # the kit computes graded relevance at scoring time.
        gains[qid] = compute_graded_relevance(gold_id, "equivalent", set(pool), hierarchy)
        sources[qid] = source_id

    by_id = lambda rows: sorted(rows, key=lambda row: row[0])  # noqa: E731
    (TASKS_DIR / "valid.cands.tsv").write_text(_tsv(["QueryID", "SrcEntity", "TgtCandidates"], by_id(cands)))
    (TASKS_DIR / "test.cands.tsv").write_text(_tsv(["QueryID", "SrcEntity", "TgtCandidates"], by_id(test)))
    (EVALUATION_DIR / TASK / "valid.answers.tsv").write_text(
        _tsv(["QueryID", "SrcEntity", "TgtEntities", "Relations", "TgtCandidates"], by_id(answers))
    )
    (EVALUATION_DIR / TASK / "valid.preferred.tsv").write_text(
        _tsv(["QueryID", "SrcEntity", "TgtEntity", "Relation"], by_id(preferred))
    )
    write_graded_relevance(EVALUATION_DIR / TASK / "valid.graded.tsv", gains, sources)
    (EVALUATION_DIR / "query_metadata.tsv").write_text(
        _tsv(["QueryID", "Task", "Split", "SrcEntity", "QueryMode"], by_id(metadata))
    )
    manifest = {"candidate_count": 50, "release": {"name": "BioKG-Align-kit-example", "version": "canonical"}, "tasks": [TASK]}
    (HERE / "release_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    write_all()
    print(f"Wrote canonical fixture to {HERE}", file=sys.stderr)
