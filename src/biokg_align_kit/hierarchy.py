"""
Hierarchy index, graded relevance, and Hierarchy-Aware Typed nDCG@10 for
the BioKG-Align kit.

This module ports a stripped-down version of the organiser-side
HierarchyIndex, compute_graded_relevance, and
hierarchy_aware_ndcg implementations. The behaviour matches the
organiser-side computation; the kit's port exists purely so participants
can compute the Hierarchy-Aware Typed nDCG@10 metric (paper §1.5)
locally without depending on the private organiser package.

Keying note
-----------
The on-disk graded-relevance TSV carries a QueryID column when
produced by the organiser pipeline. The loader and writer here both
round-trip that column. The same SrcEntity contributes both a Q0
(equivalence) and a Q1 (subsumption) query with distinct gain tables;
the per-(SrcEntity, QueryID) keying is necessary for correctness.
Files without a QueryID column fall back to "Q0" for backwards
compatibility with legacy fixtures.
"""

from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path


# Same explicit relation order as scoring.RELATION_TIEBREAK_ORDER; kept
# in this module to avoid an import cycle.
_PREFERRED_RELATION_ORDER: dict[str, int] = {
    "equivalent": 0,
    "source_subsumed_by_target": 1,
    "source_subsumes_target": 2,
}


# Relation used in graph/triples.csv to denote a (child, parent)
# subclass edge. Set organiser-side by write_graph(); kept here as the
# loader's discrimination key so unrelated relations (anchor_equivalent,
# etc.) are filtered out.
SUBCLASS_RELATION = "subclass_of"



class HierarchyIndex:
    """
    Read-only index over a directed hierarchy of (child, parent) edges.

    Supports ancestor / descendant lookups with shortest-path distance
    semantics. The shortest-path requirement matters for
    ontologies with multiple inheritance (SNOMED CT, NCIT) where the same
    ancestor can be reachable via multiple paths of different lengths —
    the graded relevance gain formula scales with distance, so the
    shortest path gives the largest gain, which is the intended
    behaviour.
    """

    def __init__(self, edges: list[dict[str, str]]) -> None:
        """
        Build the index from a list of edge dicts.

        Each edge must have child_id and parent_id keys. Other
        keys are ignored. Self-loops are silently skipped.
        """
        self.parents: dict[str, set[str]] = defaultdict(set)
        self.children: dict[str, set[str]] = defaultdict(set)
        for edge in edges:
            child = edge["child_id"]
            parent = edge["parent_id"]
            if child == parent:
                continue
            self.parents[child].add(parent)
            self.children[parent].add(child)

    def ancestors_with_distance(
        self, entity_id: str, max_distance: int
    ) -> dict[str, int]:
        """
        Return a mapping {ancestor_id: shortest_path_distance} for
        ancestors at distances 1..max_distance from entity_id
        (inclusive).

        BFS by level guarantees that the recorded distance is the
        shortest path; once recorded, distances are never revised.
        entity_id itself is never included in the result
        (distance 0 is reserved for the entity being looked up).
        """
        if max_distance < 1:
            return {}
        distances: dict[str, int] = {}
        current_level: set[str] = self.parents.get(entity_id, set()).copy()
        current_level.discard(entity_id)  # defensive against self-loops
        depth = 1
        while current_level and depth <= max_distance:
            next_level: set[str] = set()
            for node in current_level:
                if node not in distances:
                    distances[node] = depth
                    if depth < max_distance:
                        for parent in self.parents.get(node, set()):
                            if parent not in distances and parent != entity_id:
                                next_level.add(parent)
            current_level = next_level
            depth += 1
        return distances

    def descendants_with_distance(
        self, entity_id: str, max_distance: int
    ) -> dict[str, int]:
        """
        Return a mapping {descendant_id: shortest_path_distance} for
        descendants at distances 1..max_distance from entity_id.

        Symmetric to :meth:`ancestors_with_distance`; same BFS-by-level
        guarantee, same exclusion of self.
        """
        if max_distance < 1:
            return {}
        distances: dict[str, int] = {}
        current_level: set[str] = self.children.get(entity_id, set()).copy()
        current_level.discard(entity_id)
        depth = 1
        while current_level and depth <= max_distance:
            next_level: set[str] = set()
            for node in current_level:
                if node not in distances:
                    distances[node] = depth
                    if depth < max_distance:
                        for child in self.children.get(node, set()):
                            if child not in distances and child != entity_id:
                                next_level.add(child)
            current_level = next_level
            depth += 1
        return distances


def load_hierarchy_from_triples(
    triples_path: str | Path,
    subclass_relation: str = SUBCLASS_RELATION,
) -> HierarchyIndex:
    """
    Build a :class:`HierarchyIndex` from a graph/triples.csv file.

    The triples file is expected to be a CSV with at least the columns
    head_id, relation and tail_id. Rows whose relation
    matches subclass_relation are interpreted as (child, parent)
    edges where head_id = child and tail_id = parent (the
    convention used by the organiser-side write_graph writer).

    Rows with any other relation are silently skipped, so the same
    file can be passed unfiltered even though it contains
    anchor_equivalent rows and other non-hierarchy triples.
    """
    edges: list[dict[str, str]] = []
    with Path(triples_path).open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("relation") != subclass_relation:
                continue
            edges.append({
                "child_id": row["head_id"],
                "parent_id": row["tail_id"],
            })
    return HierarchyIndex(edges)


def compute_graded_relevance(
    preferred_target: str,
    preferred_relation: str,
    candidate_set: set[str],
    hierarchy: HierarchyIndex,
    max_distance: int = 3,
) -> dict[tuple[str, str], float]:
    """
    Compute graded relevance gains for the Hierarchy-Aware Typed nDCG@10
    metric (paper §1.5) for a single query.

    Gain table (only non-zero entries are returned; absence from the
    result dict denotes gain 0):

    +--------------------------+--------------+-------------+--------------+
    | Preferred (v*, r*)       | gain(v*,≡)   | gain(v*,⊑)  | gain(v*,⊒)   |
    +==========================+==============+=============+==============+
    | (v*, equivalent)         | 1.0          | 0.6         | 0.6          |
    +--------------------------+--------------+-------------+--------------+
    | (v*, ssbt)               | 0.0          | 1.0         | 0.0          |
    +--------------------------+--------------+-------------+--------------+
    | (v*, sst)                | 0.0          | 0.0         | 1.0          |
    +--------------------------+--------------+-------------+--------------+

    Where ssbt = source_subsumed_by_target, sst = source_subsumes_target.

    Hierarchical partial credit (depth d ∈ {1, ..., max_distance}):

    * Equivalence-preferred: ancestors of v* receive gain
      0.6 / (d + 1) at the ssbt relation; descendants at the sst
      relation.
    * ssbt-preferred: ancestors at 1.0 / (d + 1) at ssbt.
    * sst-preferred: descendants at 1.0 / (d + 1) at sst.

    Entities that would receive credit but aren't in candidate_set
    are silently dropped — a system cannot rank what it isn't given.
    """
    if preferred_relation not in _PREFERRED_RELATION_ORDER:
        raise ValueError(
            f"Unknown preferred_relation: {preferred_relation!r}. "
            f"Expected one of {sorted(_PREFERRED_RELATION_ORDER)}."
        )

    gains: dict[tuple[str, str], float] = {}

    if preferred_target in candidate_set:
        gains[(preferred_target, preferred_relation)] = 1.0

    if preferred_relation == "equivalent":
        if preferred_target in candidate_set:
            gains[(preferred_target, "source_subsumed_by_target")] = 0.6
            gains[(preferred_target, "source_subsumes_target")] = 0.6
        for ancestor, dist in hierarchy.ancestors_with_distance(
            preferred_target, max_distance
        ).items():
            if ancestor in candidate_set:
                gains[(ancestor, "source_subsumed_by_target")] = 0.6 / (dist + 1)
        for descendant, dist in hierarchy.descendants_with_distance(
            preferred_target, max_distance
        ).items():
            if descendant in candidate_set:
                gains[(descendant, "source_subsumes_target")] = 0.6 / (dist + 1)

    elif preferred_relation == "source_subsumed_by_target":
        for ancestor, dist in hierarchy.ancestors_with_distance(
            preferred_target, max_distance
        ).items():
            if ancestor in candidate_set:
                gains[(ancestor, "source_subsumed_by_target")] = 1.0 / (dist + 1)

    elif preferred_relation == "source_subsumes_target":
        for descendant, dist in hierarchy.descendants_with_distance(
            preferred_target, max_distance
        ).items():
            if descendant in candidate_set:
                gains[(descendant, "source_subsumes_target")] = 1.0 / (dist + 1)

    return gains


def hierarchy_aware_ndcg(
    ranked_predictions: list[dict[str, str]],
    graded_relevance: dict[tuple[str, str], float],
    k: int = 10,
) -> float:
    """
    Compute Hierarchy-Aware Typed nDCG@K against continuous graded
    relevance (paper §1.5):

      DCG@K(q) = sum_{i=1..K} gain_q(p_i) / log_2(i + 1)
      IDCG@K(q) = max achievable DCG@K (top-K gains, sorted descending)
      nDCG@K(q) = DCG@K(q) / IDCG@K(q), defined as 0 when IDCG@K(q) = 0.

    Parameters
    ----------
    ranked_predictions : list of dict
        Predictions for one query, already sorted by descending score
        with deterministic tie-breaking. Each row needs at least
        TgtEntity and Relation keys.
    graded_relevance : dict[(target, relation), gain]
        Per-query graded relevance lookup (typically the output of
        :func:`compute_graded_relevance` or
        :func:`compute_graded_relevance` for this query).
    k : int, default 10
        Cutoff for DCG@K and IDCG@K.

    Returns 0.0 when no positive gains exist for the query.
    """
    dcg = 0.0
    for i, row in enumerate(ranked_predictions[:k], start=1):
        gain = graded_relevance.get((row["TgtEntity"], row["Relation"]), 0.0)
        dcg += gain / math.log2(i + 1)

    sorted_gains = sorted(graded_relevance.values(), reverse=True)
    idcg = sum(g / math.log2(i + 1) for i, g in enumerate(sorted_gains[:k], start=1))

    if idcg == 0.0:
        return 0.0
    return dcg / idcg


def load_graded_relevance(
    path: str | Path,
) -> dict[tuple[str, str], dict[tuple[str, str], float]]:
    """
    Load a graded-relevance TSV produced organiser-side or by the kit's
    build-graded-relevance helper.

    File schema (v0.2.0):

        SrcEntity   QueryID   TgtEntity   Relation   Gain

    Only non-zero gains are present in the file; missing
    (target, relation) pairs are implicitly gain 0.

    v0.2.0 keying note
    ------------------
    Files without a QueryID column fall back to "Q0" for every
    row (legacy v0.1.2 schema). Under the pool model where the same
    SrcEntity contributes Q0 + Q1 queries with distinct gain tables,
    a missing-QueryID file silently merges the two — only use the
    legacy fallback for genuine eq-only fixtures.

    Returns
    -------
    dict[(SrcEntity, QueryID), dict[(TgtEntity, Relation), gain]]
        Outer key is the per-query (SrcEntity, QueryID) tuple; inner
        dict matches the schema of :func:`compute_graded_relevance`.
        Queries whose graded relevance is empty produce no entry.
    """
    out: dict[tuple[str, str], dict[tuple[str, str], float]] = defaultdict(dict)
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        for row in reader:
            src = row["SrcEntity"]
            query_id = row.get("QueryID", "Q0")
            out[(src, query_id)][(row["TgtEntity"], row["Relation"])] = float(
                row["Gain"]
            )
    return dict(out)


def write_graded_relevance(
    path: str | Path,
    per_query_gains: dict[tuple[str, str], dict[tuple[str, str], float]],
) -> None:
    """
    Write a graded-relevance TSV with the canonical v0.2.0 schema
    (SrcEntity TgtEntity Relation Gain plus a QueryID column).

    Only non-zero gains are emitted; queries with no positive gains
    produce no rows. Rows are sorted by
    (SrcEntity, QueryID, TgtEntity, Relation) for deterministic
    output.
    """
    rows: list[tuple[str, str, str, str, float]] = []
    for (src, query_id), gains in per_query_gains.items():
        for (tgt, rel), gain in gains.items():
            if gain == 0.0:
                continue
            rows.append((src, query_id, tgt, rel, gain))
    rows.sort(
        key=lambda r: (
            r[0],
            r[1],
            r[2],
            _PREFERRED_RELATION_ORDER.get(r[3], 99),
        )
    )

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        handle.write("SrcEntity\tQueryID\tTgtEntity\tRelation\tGain\n")
        for src, query_id, tgt, rel, gain in rows:
            handle.write(f"{src}\t{query_id}\t{tgt}\t{rel}\t{gain:.6f}\n")
