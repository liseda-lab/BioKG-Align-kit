"""
Hierarchy index, graded relevance, and Hierarchy-Aware Typed nDCG@10 for
the BioKG-Align kit.

The kit is the single implementation of the graded-relevance rule: the
organiser pipeline builds the released ``graded.tsv`` files with
:func:`compute_graded_relevance`, so participants recompute exactly what
the release ships.

Keying
------
Graded-relevance files are keyed by the opaque ``QueryID``
(``<task>-<8 hex>``); the same ``SrcEntity`` can own two queries (one per
mode) with different gain tables, so the source alone is never a key.
Schema: ``QueryID  SrcEntity  TgtEntity  Relation  Gain``.
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


DEFAULT_EQUIVALENCE_PARTIAL_GAIN = 0.6


def compute_graded_relevance(
    preferred_target: str,
    preferred_relation: str,
    candidate_set: set[str],
    hierarchy: HierarchyIndex,
    max_distance: int = 3,
    equivalence_partial_gain: float = DEFAULT_EQUIVALENCE_PARTIAL_GAIN,
) -> dict[tuple[str, str], float]:
    """
    Compute graded relevance gains for the Hierarchy-Aware Typed nDCG@10
    metric (paper §1.5) for a single query.

    Gain table (only non-zero entries are returned; absence from the
    result dict denotes gain 0):

    +--------------------------+--------------+-------------+--------------+
    | Preferred (v*, r*)       | gain(v*,≡)   | gain(v*,⊑)  | gain(v*,⊒)   |
    +==========================+==============+=============+==============+
    | (v*, equivalent)         | 1.0          | g_eq        | g_eq         |
    +--------------------------+--------------+-------------+--------------+
    | (v*, ssbt)               | 0.0          | 1.0         | 0.0          |
    +--------------------------+--------------+-------------+--------------+
    | (v*, sst)                | 0.0          | 0.0         | 1.0          |
    +--------------------------+--------------+-------------+--------------+

    Where ssbt = source_subsumed_by_target, sst = source_subsumes_target,
    and g_eq = ``equivalence_partial_gain`` (canonical 0.6).

    Hierarchical partial credit (depth d ∈ {1, ..., max_distance}):

    * Equivalence-preferred: ancestors of v* receive gain
      g_eq / (d + 1) at the ssbt relation; descendants at the sst
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
        partial = float(equivalence_partial_gain)
        if preferred_target in candidate_set:
            gains[(preferred_target, "source_subsumed_by_target")] = partial
            gains[(preferred_target, "source_subsumes_target")] = partial
        for ancestor, dist in hierarchy.ancestors_with_distance(
            preferred_target, max_distance
        ).items():
            if ancestor in candidate_set:
                gains[(ancestor, "source_subsumed_by_target")] = partial / (dist + 1)
        for descendant, dist in hierarchy.descendants_with_distance(
            preferred_target, max_distance
        ).items():
            if descendant in candidate_set:
                gains[(descendant, "source_subsumes_target")] = partial / (dist + 1)

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


GRADED_COLUMNS: tuple[str, ...] = ("QueryID", "SrcEntity", "TgtEntity", "Relation", "Gain")


def format_gain(gain: float) -> str:
    """Serialised gain: six decimals, as every released graded file."""
    return f"{gain:.6f}"


def write_graded_relevance(
    path: str | Path,
    per_query_gains: dict[str, dict[tuple[str, str], float]],
    sources: dict[str, str],
) -> None:
    """
    Write a graded-relevance TSV (``QueryID SrcEntity TgtEntity Relation
    Gain``). ``per_query_gains`` is keyed by ``QueryID``; ``sources`` maps
    each ``QueryID`` to its ``SrcEntity``.

    Only non-zero gains are emitted. Rows are sorted by (QueryID,
    TgtEntity, relation order) for deterministic output.
    """
    rows: list[tuple[str, str, str, str, float]] = []
    for query_id, gains in per_query_gains.items():
        for (tgt, rel), gain in gains.items():
            if gain == 0.0:
                continue
            rows.append((query_id, sources[query_id], tgt, rel, gain))
    rows.sort(key=lambda r: (r[0], r[2], _PREFERRED_RELATION_ORDER.get(r[3], 99)))

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open("w", encoding="utf-8", newline="") as handle:
        handle.write("\t".join(GRADED_COLUMNS) + "\n")
        for query_id, src, tgt, rel, gain in rows:
            handle.write(f"{query_id}\t{src}\t{tgt}\t{rel}\t{format_gain(gain)}\n")
