"""
Generate the canonical-shape fixture (``examples/canonical/``).

The canonical fixture has the same layout as the mini fixture but uses
``|C_q| = 50`` candidates per query — matching the official challenge
shape. Unlike the mini fixture, it is generated rather than
hand-authored, because 50-row TSVs would be unreadable as hand-written
source.

What it exercises that the mini fixture doesn't:

- The default candidate-count check in the validator (50) passes
  cleanly, not as a warning. The mini fixture has 4 candidates per
  query and exists to be human-readable, not realistic.
- The full ``50 x 3 = 150`` candidate-relation pair count per query in
  submissions, matching paper §2.1.

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

import sys
from pathlib import Path

# Resolve fixture root relative to this script.
HERE = Path(__file__).resolve().parent
GRAPH_DIR = HERE / "graph"
TASKS_DIR = HERE / "tasks" / "NCIT-DOID"
ANSWERS_DIR = HERE / "answers"


# Three queries, each with a different source NCIT class.
QUERIES: list[tuple[str, str]] = [
    ("NCIT:C001", "DOID:D001"),  # Q1: gold pair (equivalent)
    ("NCIT:C002", "DOID:D002"),  # Q2: gold pair (equivalent)
    ("NCIT:C003", "DOID:D003"),  # Q3: gold pair (equivalent)
]


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


def make_candidates() -> tuple[str, list[list[str]]]:
    """Build candidate lists; return TSV text and per-query candidate lists.

    v0.2.0 train/valid public cands.tsv schema (5-column, single primary
    gold per ADR-46/48):
        SrcEntity, QueryID, TgtEntities, Relations, TgtCandidates
    """
    header = "SrcEntity\tQueryID\tTgtEntities\tRelations\tTgtCandidates\n"
    rows: list[str] = []
    per_query_cands: list[list[str]] = []

    for source_id, gold_id in QUERIES:
        local = gold_id.split(":")[1]
        # Build the 50-candidate pool with deterministic order:
        # gold first, then hierarchy, then distractors. The kit
        # doesn't depend on order — the order is shuffled before
        # distribution to participants on the real challenge — but
        # determinism makes the fixture reproducible.
        candidates: list[str] = [gold_id]
        candidates.append(f"DOID:P{local}")
        candidates.append(f"DOID:G{local}")
        candidates.append(f"DOID:R{local}")
        for i in range(1, 4):
            candidates.append(f"DOID:C{local}_{i}")
            for j in range(1, 3):
                candidates.append(f"DOID:C{local}_{i}_{j}")
        for k in range(1, 38):
            candidates.append(f"DOID:X{local}_{k:02d}")
        assert len(candidates) == 50, f"Expected 50 candidates, got {len(candidates)}"

        per_query_cands.append(candidates)
        cands_repr = "[" + ", ".join(f"'{c}'" for c in candidates) + "]"
        rows.append(
            f"{source_id}\tQ0\t"
            f"['{gold_id}']\t['equivalent']\t{cands_repr}\n"
        )

    return header + "".join(rows), per_query_cands


def make_answers_tsv() -> str:
    """Build the answers TSV (same shape as the train/valid cands.tsv)."""
    text, _ = make_candidates()
    return text


def make_preferred() -> str:
    """Build the preferred-pair TSV (one row per query, equivalence-preferred).

    Schema (unchanged): SrcEntity, QueryID, TgtEntity, Relation
    """
    header = "SrcEntity\tQueryID\tTgtEntity\tRelation\n"
    rows = [f"{src}\tQ0\t{gold}\tequivalent\n" for src, gold in QUERIES]
    return header + "".join(rows)


def write_all() -> None:
    """Generate every fixture file and write it to disk."""
    GRAPH_DIR.mkdir(parents=True, exist_ok=True)
    TASKS_DIR.mkdir(parents=True, exist_ok=True)
    ANSWERS_DIR.mkdir(parents=True, exist_ok=True)

    (GRAPH_DIR / "properties.csv").write_text(make_properties())
    (GRAPH_DIR / "triples.csv").write_text(make_triples())

    cands_text, _ = make_candidates()
    (TASKS_DIR / "valid.cands.tsv").write_text(cands_text)
    # test.cands.tsv omits the gold + QueryID columns (matches the public
    # release shape: 2 columns, SrcEntity + TgtCandidates only).
    test_lines = ["SrcEntity\tTgtCandidates\n"]
    for line in cands_text.splitlines()[1:]:
        parts = line.split("\t")
        # v0.2.0 schema: SrcEntity, QueryID, TgtEntities, Relations,
        # TgtCandidates → 5 fields.
        src = parts[0]
        cands = parts[4]
        test_lines.append(f"{src}\t{cands}\n")
    (TASKS_DIR / "test.cands.tsv").write_text("".join(test_lines))

    (ANSWERS_DIR / "NCIT-DOID.valid.answers.tsv").write_text(make_answers_tsv())
    (ANSWERS_DIR / "NCIT-DOID.valid.preferred.tsv").write_text(make_preferred())

    # The graded-relevance file is built by the kit's CLI helper (see
    # `build-graded-relevance`), not by this script — that way the
    # committed fixture is guaranteed consistent with how the kit
    # actually computes graded relevance at scoring time. To
    # regenerate:
    #
    #   PYTHONPATH=src python3 -m biokg_align_kit build-graded-relevance \
    #     --preferred examples/canonical/answers/NCIT-DOID.valid.preferred.tsv \
    #     --candidates examples/canonical/tasks/NCIT-DOID/valid.cands.tsv \
    #     --triples examples/canonical/graph/triples.csv \
    #     --output examples/canonical/answers/NCIT-DOID.valid.graded.tsv


if __name__ == "__main__":
    write_all()
    print(f"Wrote canonical fixture to {HERE}", file=sys.stderr)
