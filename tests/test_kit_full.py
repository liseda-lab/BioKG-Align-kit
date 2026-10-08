"""Kit test suite (kit 0.4.0): ID-joined submissions, strict and lenient
loading, the shared evaluation validator, metrics, fixtures and CLI."""

from __future__ import annotations

import contextlib
import io
import random
import subprocess
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

from biokg_align_kit.baselines import predict
from biokg_align_kit.evaluation import validate_evaluation_set
from biokg_align_kit.io import parse_list, read_tsv, write_tsv
from biokg_align_kit.scoring import (
    DEFAULT_RELATIONS,
    SUBMISSION_COLUMNS,
    EvaluationSetError,
    SubmissionError,
    is_count_metric,
    load_answers,
    load_graded_relevance,
    load_preferred_pairs,
    load_query_index,
    load_query_metadata,
    load_submission,
    macro_average_tasks,
    rank_key,
    score_prediction_rows,
    score_submission,
    score_task,
)
from biokg_align_kit.validation import ValidationResult, validate_submission


REPO_ROOT = Path(__file__).resolve().parents[1]
MINI = REPO_ROOT / "examples" / "mini"
MINI_PAIRED = REPO_ROOT / "examples" / "mini_paired"
CANONICAL = REPO_ROOT / "examples" / "canonical"
TASK = "NCIT-DOID"


def _cands(root: Path, split: str = "valid") -> Path:
    return root / "tasks" / TASK / f"{split}.cands.tsv"


def _write_rows(path: Path, rows: list[dict]) -> None:
    write_tsv(path, rows, list(SUBMISSION_COLUMNS))


def _build_release(root: Path, tasks: dict[str, int], split: str = "valid", pool: int = 3) -> None:
    """A synthetic release: per task, `n` queries with `pool` candidates;
    even queries are equivalence-preferred, odd ones subsumption-preferred."""
    metadata = []
    for task, n in tasks.items():
        cands, answers, preferred, graded = [], [], [], []
        for i in range(n):
            query_id = f"{task}-{i:08x}"
            source = f"S:{task}:{i}"
            pool_ids = sorted(f"T:{task}:{i}:{j}" for j in range(pool))
            relation = "equivalent" if i % 2 == 0 else "source_subsumed_by_target"
            target = pool_ids[0]
            cands.append({"QueryID": query_id, "SrcEntity": source, "TgtCandidates": repr(pool_ids)})
            answers.append({**cands[-1], "TgtEntities": repr([target]), "Relations": repr([relation])})
            preferred.append({"QueryID": query_id, "SrcEntity": source, "TgtEntity": target, "Relation": relation})
            graded.append({"QueryID": query_id, "SrcEntity": source, "TgtEntity": target, "Relation": relation, "Gain": "1.000000"})
            metadata.append({
                "QueryID": query_id, "Task": task, "Split": split, "SrcEntity": source,
                "QueryMode": "equivalence" if relation == "equivalent" else "subsumption",
            })
        write_tsv(root / "tasks" / task / f"{split}.cands.tsv", cands, ["QueryID", "SrcEntity", "TgtCandidates"])
        ev = root / "evaluation" / task
        write_tsv(ev / f"{split}.answers.tsv", answers, ["QueryID", "SrcEntity", "TgtEntities", "Relations", "TgtCandidates"])
        write_tsv(ev / f"{split}.preferred.tsv", preferred, ["QueryID", "SrcEntity", "TgtEntity", "Relation"])
        write_tsv(ev / f"{split}.graded.tsv", graded, ["QueryID", "SrcEntity", "TgtEntity", "Relation", "Gain"])
    write_tsv(root / "evaluation" / "query_metadata.tsv", metadata, ["QueryID", "Task", "Split", "SrcEntity", "QueryMode"])


def _perfect_rows(root: Path, tasks: list[str], split: str = "valid") -> list[dict]:
    """Every typed pair of every query; the preferred pair scores 1, the rest 0."""
    rows = []
    for task in tasks:
        preferred = load_preferred_pairs(root / "evaluation" / task / f"{split}.preferred.tsv")
        for query in load_query_index([root / "tasks" / task / f"{split}.cands.tsv"]).values():
            for target in sorted(query.candidates):
                for relation in DEFAULT_RELATIONS:
                    score = 1.0 if (target, relation) == preferred[query.query_id] else 0.0
                    rows.append({"QueryID": query.query_id, "SrcEntity": query.source,
                                 "TgtEntity": target, "Relation": relation, "Score": repr(score)})
    return rows


def _rows_in_memory(rows: list[tuple[str, str, str, float]]) -> dict[str, list[dict]]:
    """(QueryID, TgtEntity, Relation, Score) tuples -> predictions_by_query."""
    grouped: dict[str, list[dict]] = {}
    for query_id, target, relation, score in rows:
        grouped.setdefault(query_id, []).append(
            {"QueryID": query_id, "SrcEntity": "S", "TgtEntity": target, "Relation": relation, "Score": float(score)}
        )
    return grouped


class KitTest(unittest.TestCase):
    def test_baseline_scores_and_validates(self) -> None:
        """End-to-end on the mini fixture: predict, verify (strict), score."""
        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "predictions.tsv"
            predict(MINI, TASK, "valid", "hybrid_lexical", predictions)
            result = validate_submission(predictions, [_cands(MINI)])
            self.assertIsInstance(result, ValidationResult)
            self.assertFalse(result.errors)
            self.assertFalse(result.warnings)
            scores = score_submission(predictions, MINI, None, "valid")
            metrics = scores[TASK]
            for key in ("diagnostic_relation_aware_ndcg_at_10", "preferred_typed_mrr",
                        "hierarchy_aware_typed_ndcg_at_10", "median_preferred_typed_rank",
                        "preferred_entity_relation_queries"):
                self.assertIn(key, metrics)
            self.assertEqual(2.0, metrics["queries"])
            self.assertEqual(metrics["preferred_typed_mrr"], scores["macro"]["preferred_typed_mrr"])

    def test_removed_lexical_baseline_raises_migration_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                predict(MINI, TASK, "valid", "lexical", Path(tmp) / "predictions.tsv")
            self.assertIn("hybrid_lexical", str(ctx.exception))


class TieBreakOrderTest(unittest.TestCase):
    """Explicit relation tie-break ordering (paper §2.1)."""

    def test_explicit_relation_order_when_score_and_target_tie(self) -> None:
        from biokg_align_kit.scoring import RELATION_TIEBREAK_ORDER

        self.assertEqual(
            {"equivalent": 0, "source_subsumed_by_target": 1, "source_subsumes_target": 2},
            RELATION_TIEBREAK_ORDER,
        )
        rows = [
            {"TgtEntity": "DOID:X", "Relation": "source_subsumes_target", "Score": 0.5},
            {"TgtEntity": "DOID:X", "Relation": "equivalent", "Score": 0.5},
            {"TgtEntity": "DOID:X", "Relation": "source_subsumed_by_target", "Score": 0.5},
        ]
        self.assertEqual(
            ["equivalent", "source_subsumed_by_target", "source_subsumes_target"],
            [r["Relation"] for r in sorted(rows, key=rank_key)],
        )

    def test_target_breaks_tie_before_relation(self) -> None:
        rows = [
            {"TgtEntity": "DOID:B", "Relation": "equivalent", "Score": 0.5},
            {"TgtEntity": "DOID:A", "Relation": "source_subsumes_target", "Score": 0.5},
        ]
        self.assertEqual(["DOID:A", "DOID:B"], [r["TgtEntity"] for r in sorted(rows, key=rank_key)])

    def test_score_dominates_tiebreaks(self) -> None:
        rows = [
            {"TgtEntity": "DOID:A", "Relation": "equivalent", "Score": 0.1},
            {"TgtEntity": "DOID:Z", "Relation": "source_subsumes_target", "Score": 0.9},
        ]
        self.assertEqual(["DOID:Z", "DOID:A"], [r["TgtEntity"] for r in sorted(rows, key=rank_key)])


class PreferredPairMetricsTest(unittest.TestCase):
    """The preferred-pair family on hand-built rankings (QueryID-keyed)."""

    def _score(self, rows, preferred):
        answers = {query_id: {pair} for query_id, pair in preferred.items()}
        graded = {query_id: {pair: 1.0} for query_id, pair in preferred.items()}
        return score_prediction_rows(_rows_in_memory(rows), answers, preferred, graded)

    def test_hit_at_rank_1(self) -> None:
        metrics = self._score(
            [("Q-A-00000001", "T1", "equivalent", 0.9), ("Q-A-00000001", "T2", "equivalent", 0.1)],
            {"Q-A-00000001": ("T1", "equivalent")},
        )
        self.assertEqual(1.0, metrics["preferred_typed_mrr"])
        self.assertEqual(1.0, metrics["preferred_typed_hits_at_1"])
        self.assertEqual(1.0, metrics["median_preferred_typed_rank"])
        self.assertEqual(1.0, metrics["preferred_typed_queries"])

    def test_hit_at_rank_3(self) -> None:
        metrics = self._score(
            [("Q-A-00000001", "T2", "equivalent", 0.9), ("Q-A-00000001", "T3", "equivalent", 0.8),
             ("Q-A-00000001", "T1", "equivalent", 0.7)],
            {"Q-A-00000001": ("T1", "equivalent")},
        )
        self.assertAlmostEqual(1.0 / 3.0, metrics["preferred_typed_mrr"])
        self.assertEqual(0.0, metrics["preferred_typed_hits_at_1"])
        self.assertEqual(1.0, metrics["preferred_typed_hits_at_5"])
        self.assertEqual(3.0, metrics["median_preferred_typed_rank"])

    def test_wrong_relation_on_right_target_is_a_miss_at_rank_1(self) -> None:
        metrics = self._score(
            [("Q-A-00000001", "T1", "source_subsumed_by_target", 0.9), ("Q-A-00000001", "T1", "equivalent", 0.5)],
            {"Q-A-00000001": ("T1", "equivalent")},
        )
        self.assertEqual(0.5, metrics["preferred_typed_mrr"])

    def test_macro_across_two_queries_and_median(self) -> None:
        metrics = self._score(
            [("Q-A-00000001", "T1", "equivalent", 0.9), ("Q-A-00000001", "T2", "equivalent", 0.1),
             ("Q-A-00000002", "T2", "equivalent", 0.9), ("Q-A-00000002", "T1", "equivalent", 0.1)],
            {"Q-A-00000001": ("T1", "equivalent"), "Q-A-00000002": ("T1", "equivalent")},
        )
        self.assertAlmostEqual(0.75, metrics["preferred_typed_mrr"])
        self.assertAlmostEqual(1.5, metrics["median_preferred_typed_rank"])
        self.assertEqual(2.0, metrics["queries"])


class PreferredEntityRelationMacroF1Test(unittest.TestCase):
    """Relation Macro-F1 on the preferred entity, gated on entity correctness."""

    def _score(self, rows, preferred):
        answers = {query_id: {pair} for query_id, pair in preferred.items()}
        return score_prediction_rows(_rows_in_memory(rows), answers, preferred, {})

    def test_correct_entity_correct_relation_scores_one(self) -> None:
        metrics = self._score(
            [("Q", "T1", "equivalent", 0.9), ("Q", "T1", "source_subsumed_by_target", 0.5), ("Q", "T2", "equivalent", 0.3)],
            {"Q": ("T1", "equivalent")},
        )
        self.assertEqual(1.0, metrics["preferred_entity_relation_accuracy"])
        self.assertEqual(1.0, metrics["preferred_entity_relation_macro_f1"])
        self.assertEqual(1.0, metrics["preferred_entity_relation_queries"])

    def test_wrong_entity_excludes_query(self) -> None:
        metrics = self._score([("Q", "T2", "equivalent", 0.9), ("Q", "T1", "equivalent", 0.3)], {"Q": ("T1", "equivalent")})
        self.assertEqual(0.0, metrics["preferred_entity_relation_queries"])
        self.assertEqual(0.0, metrics["preferred_entity_relation_macro_f1"])
        self.assertEqual(0.0, metrics["preferred_entity_relation_accuracy"])

    def test_correct_entity_wrong_relation(self) -> None:
        metrics = self._score(
            [("Q", "T1", "source_subsumed_by_target", 0.9), ("Q", "T1", "equivalent", 0.5)],
            {"Q": ("T1", "equivalent")},
        )
        self.assertEqual(1.0, metrics["preferred_entity_relation_queries"])
        self.assertEqual(0.0, metrics["preferred_entity_relation_accuracy"])
        self.assertEqual(0.0, metrics["preferred_entity_relation_macro_f1"])

    def test_collapse_across_relations_picks_max_score(self) -> None:
        metrics = self._score(
            [("Q", "T1", "equivalent", 0.95), ("Q", "T1", "source_subsumed_by_target", 0.0),
             ("Q", "T2", "equivalent", 0.7), ("Q", "T2", "source_subsumed_by_target", 0.7),
             ("Q", "T2", "source_subsumes_target", 0.7)],
            {"Q": ("T1", "equivalent")},
        )
        self.assertEqual(1.0, metrics["preferred_entity_relation_queries"])
        self.assertEqual(1.0, metrics["preferred_entity_relation_accuracy"])

    def test_macro_over_multiple_queries_and_relations(self) -> None:
        """eq: TP=1 FN=1 -> F1 2/3; ssbt: TP=1 FP=1 -> F1 2/3; macro 2/3."""
        metrics = self._score(
            [("Q1", "T1", "equivalent", 0.9), ("Q2", "T1", "source_subsumed_by_target", 0.9),
             ("Q3", "T1", "source_subsumed_by_target", 0.9), ("Q3", "T1", "equivalent", 0.5)],
            {"Q1": ("T1", "equivalent"), "Q2": ("T1", "source_subsumed_by_target"), "Q3": ("T1", "equivalent")},
        )
        self.assertEqual(3.0, metrics["preferred_entity_relation_queries"])
        self.assertAlmostEqual(2.0 / 3.0, metrics["preferred_entity_relation_accuracy"])
        self.assertAlmostEqual(2.0 / 3.0, metrics["preferred_entity_relation_macro_f1"])


class HierarchyIndexTest(unittest.TestCase):
    """Coverage for the hierarchy index and loaders."""

    def test_ancestors_with_distance(self) -> None:
        from biokg_align_kit.hierarchy import HierarchyIndex

        # A → B → C → D (linear chain).
        idx = HierarchyIndex([
            {"child_id": "A", "parent_id": "B"},
            {"child_id": "B", "parent_id": "C"},
            {"child_id": "C", "parent_id": "D"},
        ])
        # With max_distance=2, D should be cut off.
        self.assertEqual(idx.ancestors_with_distance("A", 2), {"B": 1, "C": 2})
        self.assertEqual(idx.ancestors_with_distance("A", 3), {"B": 1, "C": 2, "D": 3})
        # Leaf node has no ancestors when max_distance=0.
        self.assertEqual(idx.ancestors_with_distance("A", 0), {})

    def test_descendants_with_distance(self) -> None:
        from biokg_align_kit.hierarchy import HierarchyIndex

        # D ← C ← B ← A (linear chain, opposite direction).
        idx = HierarchyIndex([
            {"child_id": "A", "parent_id": "B"},
            {"child_id": "B", "parent_id": "C"},
            {"child_id": "C", "parent_id": "D"},
        ])
        # Descendants of D within distance 2: C (d=1) and B (d=2).
        self.assertEqual(idx.descendants_with_distance("D", 2), {"C": 1, "B": 2})

    def test_shortest_path_in_multi_parent_hierarchy(self) -> None:
        """
        Multiple-inheritance case: when A has two paths to C (one short,
        one long), the index records the SHORTEST distance.
        """
        from biokg_align_kit.hierarchy import HierarchyIndex

        # A → C (short, distance 1)
        # A → B → C (long, distance 2)
        idx = HierarchyIndex([
            {"child_id": "A", "parent_id": "C"},
            {"child_id": "A", "parent_id": "B"},
            {"child_id": "B", "parent_id": "C"},
        ])
        self.assertEqual(idx.ancestors_with_distance("A", 3), {"C": 1, "B": 1})

    def test_self_loops_dropped(self) -> None:
        from biokg_align_kit.hierarchy import HierarchyIndex

        idx = HierarchyIndex([
            {"child_id": "A", "parent_id": "A"},
            {"child_id": "A", "parent_id": "B"},
        ])
        self.assertEqual(idx.ancestors_with_distance("A", 3), {"B": 1})
        self.assertNotIn("A", idx.ancestors_with_distance("A", 3))

    def test_load_hierarchy_from_triples_filters_subclass_only(self) -> None:
        """Only relation == 'subclass_of' rows form the hierarchy."""
        from biokg_align_kit.hierarchy import load_hierarchy_from_triples

        with tempfile.TemporaryDirectory() as tmp:
            triples = Path(tmp) / "triples.csv"
            triples.write_text(
                "head_id,relation,tail_id\n"
                "A,subclass_of,B\n"
                "C,subclass_of,B\n"
                "X,anchor_equivalent,Y\n"  # not a hierarchy edge — must be ignored
            )
            idx = load_hierarchy_from_triples(triples)
            self.assertEqual(idx.ancestors_with_distance("A", 1), {"B": 1})
            self.assertEqual(idx.ancestors_with_distance("X", 1), {})


class ComputeGradedRelevanceTest(unittest.TestCase):
    """Coverage for the compute_graded_relevance gain table."""

    @staticmethod
    def _toy_hierarchy():
        """
        Build a small hierarchy used across the test methods:

            D000 (root) ── parent of D001, D002
            D004 ── child of D001 (one level below the preferred target)
            D003 ── unrelated entity

        Distances from D001: ancestors {D000: 1}, descendants {D004: 1}.
        Distances from D002: ancestors {D000: 1}, descendants {}.
        """
        from biokg_align_kit.hierarchy import HierarchyIndex
        return HierarchyIndex([
            {"child_id": "D001", "parent_id": "D000"},
            {"child_id": "D002", "parent_id": "D000"},
            {"child_id": "D004", "parent_id": "D001"},
        ])

    def test_equivalence_preferred_gains(self) -> None:
        """When preferred = (D001, equivalent), expected gains:
        - (D001, equivalent): 1.0
        - (D001, ssbt): 0.6
        - (D001, sst): 0.6
        - (D000, ssbt): 0.6/2 = 0.3 (ancestor at distance 1)
        - (D004, sst):  0.6/2 = 0.3 (descendant at distance 1)
        - D003 unrelated: 0.0 (omitted)
        """
        from biokg_align_kit.hierarchy import compute_graded_relevance

        gains = compute_graded_relevance(
            preferred_target="D001",
            preferred_relation="equivalent",
            candidate_set={"D000", "D001", "D002", "D003", "D004"},
            hierarchy=self._toy_hierarchy(),
            max_distance=3,
        )
        self.assertEqual(gains[("D001", "equivalent")], 1.0)
        self.assertEqual(gains[("D001", "source_subsumed_by_target")], 0.6)
        self.assertEqual(gains[("D001", "source_subsumes_target")], 0.6)
        self.assertAlmostEqual(gains[("D000", "source_subsumed_by_target")], 0.3)
        self.assertAlmostEqual(gains[("D004", "source_subsumes_target")], 0.3)
        # D003 unrelated → no entry
        self.assertNotIn(("D003", "equivalent"), gains)
        self.assertNotIn(("D003", "source_subsumed_by_target"), gains)
        # D002 is a sibling, not an ancestor / descendant → no entry
        self.assertNotIn(("D002", "source_subsumed_by_target"), gains)
        self.assertNotIn(("D002", "source_subsumes_target"), gains)

    def test_ssbt_preferred_gains(self) -> None:
        """When preferred = (D001, ssbt), expected gains:
        - (D001, ssbt): 1.0
        - (D000, ssbt): 1.0/2 = 0.5
        Nothing else: no same-entity partial credit on the other two
        relations; descendants don't apply for ssbt.
        """
        from biokg_align_kit.hierarchy import compute_graded_relevance

        gains = compute_graded_relevance(
            preferred_target="D001",
            preferred_relation="source_subsumed_by_target",
            candidate_set={"D000", "D001", "D002", "D003", "D004"},
            hierarchy=self._toy_hierarchy(),
            max_distance=3,
        )
        self.assertEqual(gains[("D001", "source_subsumed_by_target")], 1.0)
        self.assertAlmostEqual(gains[("D000", "source_subsumed_by_target")], 0.5)
        # No same-entity equivalence or sst credit for ssbt-preferred.
        self.assertNotIn(("D001", "equivalent"), gains)
        self.assertNotIn(("D001", "source_subsumes_target"), gains)
        # Descendants don't get credit when preferred is ssbt.
        self.assertNotIn(("D004", "source_subsumed_by_target"), gains)

    def test_sst_preferred_gains(self) -> None:
        """Symmetric to ssbt: only descendants get credit."""
        from biokg_align_kit.hierarchy import compute_graded_relevance

        gains = compute_graded_relevance(
            preferred_target="D001",
            preferred_relation="source_subsumes_target",
            candidate_set={"D000", "D001", "D002", "D003", "D004"},
            hierarchy=self._toy_hierarchy(),
            max_distance=3,
        )
        self.assertEqual(gains[("D001", "source_subsumes_target")], 1.0)
        self.assertAlmostEqual(gains[("D004", "source_subsumes_target")], 0.5)
        # No same-entity credit on other relations.
        self.assertNotIn(("D001", "equivalent"), gains)
        self.assertNotIn(("D001", "source_subsumed_by_target"), gains)
        # Ancestors don't get credit when preferred is sst.
        self.assertNotIn(("D000", "source_subsumes_target"), gains)

    def test_candidates_not_in_set_are_dropped(self) -> None:
        """Entities outside the candidate set get no entry."""
        from biokg_align_kit.hierarchy import compute_graded_relevance

        gains = compute_graded_relevance(
            preferred_target="D001",
            preferred_relation="equivalent",
            candidate_set={"D001"},  # only the preferred itself
            hierarchy=self._toy_hierarchy(),
            max_distance=3,
        )
        # Should only contain entries for D001.
        self.assertEqual(set(t for (t, _) in gains.keys()), {"D001"})
        # Ancestor D000 not in candidate set, even though it would
        # have positive gain otherwise.
        self.assertNotIn(("D000", "source_subsumed_by_target"), gains)

    def test_unknown_relation_raises(self) -> None:
        from biokg_align_kit.hierarchy import compute_graded_relevance

        with self.assertRaises(ValueError):
            compute_graded_relevance(
                preferred_target="D001",
                preferred_relation="nonsense",
                candidate_set={"D001"},
                hierarchy=self._toy_hierarchy(),
                max_distance=3,
            )


class HierarchyAwareNdcgTest(unittest.TestCase):
    """Coverage for the hierarchy_aware_ndcg function."""

    def test_perfect_ranking_returns_1(self) -> None:
        """When predictions match IDCG order, nDCG@K = 1.0."""
        from biokg_align_kit.hierarchy import hierarchy_aware_ndcg

        gains = {
            ("T1", "equivalent"): 1.0,
            ("T1", "source_subsumed_by_target"): 0.6,
            ("T2", "source_subsumed_by_target"): 0.3,
        }
        # Predict pairs in descending-gain order.
        ranked = [
            {"TgtEntity": "T1", "Relation": "equivalent"},
            {"TgtEntity": "T1", "Relation": "source_subsumed_by_target"},
            {"TgtEntity": "T2", "Relation": "source_subsumed_by_target"},
            {"TgtEntity": "T9", "Relation": "equivalent"},  # gain 0
        ]
        self.assertAlmostEqual(hierarchy_aware_ndcg(ranked, gains, k=10), 1.0)

    def test_empty_gains_returns_zero(self) -> None:
        """A query with no positive gains contributes 0."""
        from biokg_align_kit.hierarchy import hierarchy_aware_ndcg

        ranked = [
            {"TgtEntity": "T1", "Relation": "equivalent"},
            {"TgtEntity": "T2", "Relation": "equivalent"},
        ]
        self.assertEqual(hierarchy_aware_ndcg(ranked, {}, k=10), 0.0)

    def test_hand_computed_value(self) -> None:
        """
        Hand-computed reference value:
        - gains = {(T1, equivalent): 1.0, (T2, equivalent): 0.5}
        - ranked: [(T2, equivalent), (T1, equivalent)]
        - DCG@2  = 0.5/log2(2) + 1.0/log2(3) = 0.5 + 0.6309 ≈ 1.13093
        - IDCG@2 = 1.0/log2(2) + 0.5/log2(3) = 1.0 + 0.31546 ≈ 1.31546
        - nDCG@2 ≈ 0.85969
        """
        from biokg_align_kit.hierarchy import hierarchy_aware_ndcg

        gains = {
            ("T1", "equivalent"): 1.0,
            ("T2", "equivalent"): 0.5,
        }
        ranked = [
            {"TgtEntity": "T2", "Relation": "equivalent"},
            {"TgtEntity": "T1", "Relation": "equivalent"},
        ]
        self.assertAlmostEqual(
            hierarchy_aware_ndcg(ranked, gains, k=2), 0.8597, places=3
        )



class DatalogLoaderTest(unittest.TestCase):
    """
    Coverage for the minimal Datalog facts/rules loader (P2 Patch 12).

    The loader is syntactic only: it parses files into typed Python
    data structures but does not evaluate rules. These tests cover the
    parsing surface plus a few edge cases participants might hit.
    """

    def test_load_facts_basic(self) -> None:
        from biokg_align_kit.datalog import load_facts

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "facts.dl"
            path.write_text(
                "% Example facts file\n"
                "subclass(NCIT_C001, NCIT_C002).\n"
                "edge(DOID_D001, partOf, DOID_D002).\n"
                "\n"
                "equiv(NCIT_C001, DOID_D001).\n"
            )
            facts = load_facts(path)
            self.assertEqual(len(facts), 3)
            self.assertEqual(facts[0].atom.predicate, "subclass")
            self.assertEqual(facts[0].atom.args, ("NCIT_C001", "NCIT_C002"))
            self.assertEqual(facts[1].atom.predicate, "edge")
            self.assertEqual(facts[1].atom.args, ("DOID_D001", "partOf", "DOID_D002"))

    def test_load_facts_missing_period_raises(self) -> None:
        from biokg_align_kit.datalog import load_facts

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.dl"
            path.write_text("subclass(A, B)\n")  # no terminating period
            with self.assertRaises(ValueError) as ctx:
                load_facts(path)
            self.assertIn("end with '.'", str(ctx.exception))

    def test_load_rules_basic(self) -> None:
        from biokg_align_kit.datalog import load_rules

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.dl"
            path.write_text(
                "% Transitivity of subclass\n"
                "subclass(X, Z) :- subclass(X, Y), subclass(Y, Z).\n"
                "% Equivalence is symmetric\n"
                "equiv(Y, X) :- equiv(X, Y).\n"
            )
            rules = load_rules(path)
            self.assertEqual(len(rules), 2)

            t = rules[0]
            self.assertEqual(t.head.predicate, "subclass")
            self.assertEqual(t.head.args, ("X", "Z"))
            self.assertEqual(len(t.body), 2)
            self.assertEqual(t.body[0].args, ("X", "Y"))
            self.assertEqual(t.body[1].args, ("Y", "Z"))

            sym = rules[1]
            self.assertEqual(sym.head.args, ("Y", "X"))
            self.assertEqual(sym.body[0].args, ("X", "Y"))

    def test_split_body_respects_parens(self) -> None:
        """
        Commas inside parenthesised argument lists must not split body
        atoms. Critical when arguments contain compound terms or when
        future BioKG-Align schemas use n-ary predicates with internal
        commas in printed args.
        """
        from biokg_align_kit.datalog import load_rules

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.dl"
            # Three body atoms; the middle one has 3 args.
            path.write_text("p(X) :- a(X), b(X, Y, Z), c(X).\n")
            rules = load_rules(path)
            self.assertEqual(len(rules), 1)
            self.assertEqual(len(rules[0].body), 3)
            self.assertEqual(rules[0].body[1].predicate, "b")
            self.assertEqual(rules[0].body[1].args, ("X", "Y", "Z"))

    def test_zero_arity_atom(self) -> None:
        """Predicates with no arguments parse cleanly."""
        from biokg_align_kit.datalog import load_facts

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "facts.dl"
            path.write_text("loaded().\n")
            facts = load_facts(path)
            self.assertEqual(facts[0].atom.predicate, "loaded")
            self.assertEqual(facts[0].atom.args, ())

    def test_round_trip_via_str(self) -> None:
        """str(Fact) and str(Rule) produce parseable output."""
        from biokg_align_kit.datalog import (
            Atom, Fact, Rule, load_facts, load_rules,
        )

        fact = Fact(atom=Atom("subclass", ("A", "B")))
        rule = Rule(
            head=Atom("p", ("X",)),
            body=(Atom("a", ("X",)), Atom("b", ("X", "Y"))),
        )
        with tempfile.TemporaryDirectory() as tmp:
            facts_path = Path(tmp) / "facts.dl"
            rules_path = Path(tmp) / "rules.dl"
            facts_path.write_text(str(fact) + "\n")
            rules_path.write_text(str(rule) + "\n")

            self.assertEqual(load_facts(facts_path), [fact])
            self.assertEqual(load_rules(rules_path), [rule])

    def test_load_program_handles_souffle_directives_includes_comparisons_and_terms(self) -> None:
        from biokg_align_kit.datalog import Comparison, load_program, load_terms, decode_argument

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "facts.dl").write_text('source_triple("g", "s", "p", "o,with,commas").\n', encoding="utf-8")
            (root / "rules.dl").write_text(
                '.decl source_triple(g:symbol, s:symbol, p:symbol, o:symbol)\n'
                '.include "facts.dl"\n'
                'triple(s, p, o) :- source_triple(g, s, p, o), s != o. // comparison\n'
                '.output triple\n',
                encoding="utf-8",
            )
            (root / "datalog_terms.tsv").write_text(
                "term_id\tterm_type\tlexical\tdatatype\tlanguage\tntriples\n"
                "s\tiri\thttp://example.org/s\t\t\t<http://example.org/s>\n",
                encoding="utf-8",
            )
            program = load_program(root / "rules.dl")
            self.assertEqual(1, len(program.facts))
            self.assertEqual("o,with,commas", program.facts[0].atom.args[3].strip('"'))
            self.assertIsInstance(program.rules[0].body[-1], Comparison)
            terms = load_terms(root / "datalog_terms.tsv")
            self.assertEqual("http://example.org/s", decode_argument('"s"', terms).lexical)



class EquivalencePartialGainTest(unittest.TestCase):
    def test_gain_parameter_scales_same_entity_and_hierarchy_credit(self) -> None:
        from biokg_align_kit.hierarchy import HierarchyIndex, compute_graded_relevance

        hierarchy = HierarchyIndex([{"child_id": "D001", "parent_id": "D000"}])
        default = compute_graded_relevance("D001", "equivalent", {"D000", "D001"}, hierarchy)
        scaled = compute_graded_relevance("D001", "equivalent", {"D000", "D001"}, hierarchy, equivalence_partial_gain=0.9)
        self.assertEqual(0.6, default[("D001", "source_subsumed_by_target")])
        self.assertEqual(0.9, scaled[("D001", "source_subsumed_by_target")])
        self.assertAlmostEqual(0.45, scaled[("D000", "source_subsumed_by_target")])
        self.assertEqual(1.0, scaled[("D001", "equivalent")])
        # subsumption-preferred gains do not depend on the parameter
        self.assertEqual(
            compute_graded_relevance("D001", "source_subsumed_by_target", {"D000", "D001"}, hierarchy),
            compute_graded_relevance("D001", "source_subsumed_by_target", {"D000", "D001"}, hierarchy, equivalence_partial_gain=0.3),
        )


class BuildGradedRelevanceCliTest(unittest.TestCase):
    def test_regenerates_the_committed_fixture_graded_files(self) -> None:
        for fixture in (MINI, MINI_PAIRED, CANONICAL):
            committed = (fixture / "evaluation" / TASK / "valid.graded.tsv").read_text()
            with tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "regen.graded.tsv"
                result = subprocess.run(
                    [sys.executable, "-m", "biokg_align_kit", "build-graded-relevance",
                     "--preferred", str(fixture / "evaluation" / TASK / "valid.preferred.tsv"),
                     "--candidates", str(_cands(fixture)),
                     "--triples", str(fixture / "graph" / "triples.csv"),
                     "--output", str(output)],
                    env={"PYTHONPATH": str(REPO_ROOT / "src"), "PATH": "/usr/bin:/bin"},
                    capture_output=True, text=True,
                )
                self.assertEqual(0, result.returncode, msg=result.stderr)
                self.assertEqual(committed, output.read_text(), fixture.name)

    def test_equivalence_partial_gain_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "g.tsv"
            subprocess.run(
                [sys.executable, "-m", "biokg_align_kit", "build-graded-relevance",
                 "--preferred", str(MINI / "evaluation" / TASK / "valid.preferred.tsv"),
                 "--candidates", str(_cands(MINI)), "--triples", str(MINI / "graph" / "triples.csv"),
                 "--output", str(output), "--equivalence-partial-gain", "0.9", "--max-distance", "1"],
                env={"PYTHONPATH": str(REPO_ROOT / "src"), "PATH": "/usr/bin:/bin"}, check=True, capture_output=True,
            )
            gains = load_graded_relevance(output)
            values = {gain for query in gains.values() for gain in query.values()}
            self.assertIn(0.9, values)
            self.assertIn(0.45, values)


class BaselineNamingTest(unittest.TestCase):
    def test_legacy_lexical_name_raises_helpful_error(self) -> None:
        from biokg_align_kit.baselines import score

        with self.assertRaises(ValueError) as ctx:
            score("s", "t", "equivalent", {}, "lexical", seed=17)
        self.assertIn("hybrid_lexical", str(ctx.exception))
        self.assertIn("renamed", str(ctx.exception).lower())

    def test_baseline_writes_the_five_column_submission(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "predictions.tsv"
            predict(MINI, TASK, "valid", "hybrid_lexical", predictions)
            header = predictions.read_text().splitlines()[0].split("\t")
            self.assertEqual(list(SUBMISSION_COLUMNS), header)
            rows = read_tsv(predictions)
            index = load_query_index([_cands(MINI)])
            self.assertEqual(set(index), {row["QueryID"] for row in rows})
            self.assertEqual(sum(len(q.candidates) * 3 for q in index.values()), len(rows))


class PairedQueryKeyingTest(unittest.TestCase):
    """mini_paired: two sources x two queries. Everything is keyed by the
    opaque QueryID, so a source's two queries never merge; their pools differ
    (the subsumption-mode pool excludes the equivalence target)."""

    def test_loaders_keep_both_queries_of_a_source(self) -> None:
        ev = MINI_PAIRED / "evaluation" / TASK
        preferred = load_preferred_pairs(ev / "valid.preferred.tsv")
        answers = load_answers(ev / "valid.answers.tsv")
        graded = load_graded_relevance(ev / "valid.graded.tsv")
        index = load_query_index([ev / "valid.answers.tsv"])
        self.assertEqual(4, len(preferred))
        self.assertEqual(4, len(answers))
        self.assertEqual(4, len(graded))
        by_source: dict[str, list[str]] = {}
        for query in index.values():
            by_source.setdefault(query.source, []).append(query.query_id)
        self.assertEqual({2}, {len(ids) for ids in by_source.values()})
        for ids in by_source.values():
            relations = sorted(preferred[query_id][1] for query_id in ids)
            self.assertEqual(["equivalent", "source_subsumed_by_target"], relations)
            first, second = (index[query_id].candidates for query_id in ids)
            self.assertNotEqual(first, second, "the two queries of a source must not share a pool")

    def test_end_to_end_scoring_counts_four_queries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "predictions.tsv"
            predict(MINI_PAIRED, TASK, "valid", "hybrid_lexical", predictions)
            metrics = score_submission(predictions, MINI_PAIRED, None, "valid")[TASK]
            self.assertEqual(4.0, metrics["queries"])
            self.assertEqual(4.0, metrics["preferred_typed_queries"])
            self.assertEqual(4.0, metrics["hierarchy_aware_typed_ndcg_at_10_queries"])


class CanonicalFixtureTest(unittest.TestCase):
    """examples/canonical: |C_q| = 50, generated by a committed script."""

    def test_baseline_verifies_against_valid_and_test_cands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for split in ("valid", "test"):
                predictions = Path(tmp) / f"{split}.tsv"
                predict(CANONICAL, TASK, split, "hybrid_lexical", predictions)
                self.assertFalse(validate_submission(predictions, [_cands(CANONICAL, split)]).errors)

    def test_end_to_end_emits_all_metric_families(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "predictions.tsv"
            predict(CANONICAL, TASK, "valid", "hybrid_lexical", predictions)
            metrics = score_submission(predictions, CANONICAL, None, "valid")[TASK]
            self.assertIn("diagnostic_relation_aware_ndcg_at_10", metrics)
            self.assertEqual(3.0, metrics["queries"])
            self.assertEqual(3.0, metrics["preferred_typed_queries"])
            self.assertEqual(3.0, metrics["hierarchy_aware_typed_ndcg_at_10_queries"])

    def test_candidate_count_per_query_is_exactly_50_and_sorted(self) -> None:
        import json

        self.assertEqual(50, json.loads((CANONICAL / "release_manifest.json").read_text())["candidate_count"])
        for split in ("valid", "test"):
            for row in read_tsv(_cands(CANONICAL, split)):
                candidates = parse_list(row["TgtCandidates"])
                self.assertEqual(50, len(candidates))
                self.assertEqual(sorted(candidates), candidates)

    def test_generator_is_deterministic(self) -> None:
        generated = [
            "graph/properties.csv", "graph/triples.csv", "tasks/NCIT-DOID/valid.cands.tsv",
            "tasks/NCIT-DOID/test.cands.tsv", "evaluation/NCIT-DOID/valid.answers.tsv",
            "evaluation/NCIT-DOID/valid.preferred.tsv", "evaluation/NCIT-DOID/valid.graded.tsv",
            "evaluation/query_metadata.tsv", "release_manifest.json",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "_build.py"
            script.write_text((CANONICAL / "_build_canonical_fixture.py").read_text())
            result = subprocess.run(
                [sys.executable, str(script)], capture_output=True, text=True,
                env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO_ROOT / "src")},
            )
            self.assertEqual(0, result.returncode, msg=result.stderr)
            for relative in generated:
                self.assertEqual(
                    (CANONICAL / relative).read_text(), (Path(tmp) / relative).read_text(),
                    f"{relative} differs from the committed fixture; regenerate it",
                )


class SubmissionValidatorTest(unittest.TestCase):
    """verify / validate_submission run the strict loader: every rule fatal."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.predictions = self.tmp / "predictions.tsv"
        predict(MINI_PAIRED, TASK, "valid", "hybrid_lexical", self.predictions)
        self.rows = read_tsv(self.predictions)
        self.cands = [_cands(MINI_PAIRED)]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _errors(self, rows: list[dict]) -> str:
        path = self.tmp / "edited.tsv"
        _write_rows(path, rows)
        result = validate_submission(path, self.cands)
        self.assertTrue(result.errors, "expected the submission to be rejected")
        self.assertFalse(result.warnings)
        return "\n".join(result.errors)

    def test_perfect_submission_passes(self) -> None:
        result = validate_submission(self.predictions, self.cands)
        self.assertFalse(result.errors)
        self.assertFalse(result.warnings)

    def test_legacy_four_column_header_is_rejected(self) -> None:
        path = self.tmp / "legacy.tsv"
        write_tsv(path, self.rows, ["SrcEntity", "TgtEntity", "Relation", "Score"])
        errors = "\n".join(validate_submission(path, self.cands).errors)
        self.assertIn("[header]", errors)
        self.assertIn("QueryID", errors)

    def test_each_rule_is_fatal(self) -> None:
        index = load_query_index(self.cands)
        query_id = self.rows[0]["QueryID"]
        other_pool_target = next(
            t for q in index.values() if q.source == index[query_id].source and q.query_id != query_id
            for t in q.candidates if t not in index[query_id].candidates
        )
        cases = {
            "unknown_query_id": lambda rows: [{**rows[0], "QueryID": "NCIT-DOID-ffffffff"}] + rows[1:],
            "missing_query": lambda rows: [r for r in rows if r["QueryID"] != query_id],
            "source_mismatch": lambda rows: [{**rows[0], "SrcEntity": "NCIT:C999"}] + rows[1:],
            "off_pool_target": lambda rows: [{**rows[0], "TgtEntity": other_pool_target}] + rows[1:],
            "bad_relation": lambda rows: [{**rows[0], "Relation": "related_to"}] + rows[1:],
            "bad_score": lambda rows: [{**rows[0], "Score": "nan"}] + rows[1:],
            "duplicate_pair": lambda rows: rows + [dict(rows[0])],
            "incomplete_query": lambda rows: rows[1:],
        }
        for rule, edit in cases.items():
            with self.subTest(rule=rule):
                self.assertIn(f"[{rule}]", self._errors(edit(self.rows)))
        self.assertIn("[bad_score]", self._errors([{**self.rows[0], "Score": "high"}] + self.rows[1:]))
        self.assertIn("[bad_score]", self._errors([{**self.rows[0], "Score": "inf"}] + self.rows[1:]))

    def test_malformed_row_is_fatal(self) -> None:
        path = self.tmp / "malformed.tsv"
        _write_rows(path, self.rows)
        with path.open("a") as handle:
            handle.write("NCIT-DOID-1a8109a3\tNCIT:C001\tDOID:D001\n")
        self.assertIn("[malformed_row]", "\n".join(validate_submission(path, self.cands).errors))


class SubmissionIdentityTest(unittest.TestCase):
    """The §4.4 cases (the v0.3.2 swap probe), strict and lenient, through the
    loader and through score_submission."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.baseline = self.tmp / "baseline.tsv"
        predict(MINI_PAIRED, TASK, "valid", "hybrid_lexical", self.baseline)
        self.rows = read_tsv(self.baseline)
        self.index = load_query_index([_cands(MINI_PAIRED)])
        by_source: dict[str, list[str]] = {}
        for query in self.index.values():
            by_source.setdefault(query.source, []).append(query.query_id)
        self.pair = sorted(by_source["NCIT:C001"])

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, name: str, rows: list[dict], columns=SUBMISSION_COLUMNS) -> Path:
        path = self.tmp / name
        write_tsv(path, rows, list(columns))
        return path

    def _id_swapped(self) -> list[dict]:
        a, b = self.pair
        return [{**row, "QueryID": {a: b, b: a}.get(row["QueryID"], row["QueryID"])} for row in self.rows]

    def _cases(self) -> dict[str, Path]:
        shuffled = list(self.rows)
        random.Random(7).shuffle(shuffled)
        return {
            "a_id_swap": self._write("a.tsv", self._id_swapped()),
            "b_permuted": self._write("b.tsv", shuffled),
            "c_dropped_row": self._write("c.tsv", self.rows[1:]),
            "d_renamed_id": self._write("d.tsv", [{**r, "QueryID": "NCIT-DOID-00c0ffee"} if r["QueryID"] == self.pair[0] else r for r in self.rows]),
            "e_duplicate_row": self._write("e.tsv", self.rows + [self.rows[5]]),
            "f_legacy": self._write("f.tsv", self.rows, ["SrcEntity", "TgtEntity", "Relation", "Score"]),
        }

    def test_strict_mode(self) -> None:
        cases = self._cases()
        baseline_metrics = score_submission(self.baseline, MINI_PAIRED, None, "valid")
        for name, path in cases.items():
            with self.subTest(case=name):
                if name == "b_permuted":
                    self.assertEqual(baseline_metrics, score_submission(path, MINI_PAIRED, None, "valid"))
                    continue
                with self.assertRaises(SubmissionError) as loader_ctx:
                    load_submission(path, self.index, strict=True)
                with self.assertRaises(SubmissionError):
                    score_submission(path, MINI_PAIRED, None, "valid", strict=True)
                message = str(loader_ctx.exception)
                if name == "a_id_swap":
                    self.assertIn("[off_pool_target]", message)
                    self.assertIn(self.pair[0], message)
                if name == "c_dropped_row":
                    self.assertIn("[incomplete_query]", message)
                if name == "d_renamed_id":
                    self.assertIn("NCIT-DOID-00c0ffee", message)
                    self.assertIn("[unknown_query_id]", message)
                if name == "e_duplicate_row":
                    self.assertIn("[duplicate_pair]", message)
                if name == "f_legacy":
                    self.assertIn("[header]", message)

    def test_lenient_mode(self) -> None:
        cases = self._cases()
        baseline_metrics = score_submission(self.baseline, MINI_PAIRED, None, "valid", strict=False)
        for name, path in cases.items():
            with self.subTest(case=name):
                if name == "f_legacy":
                    with self.assertRaises(SubmissionError):
                        score_submission(path, MINI_PAIRED, None, "valid", strict=False)
                    continue
                with warnings.catch_warnings(record=True) as caught:
                    warnings.simplefilter("always")
                    loaded = load_submission(path, self.index, strict=False)
                    result = score_submission(path, MINI_PAIRED, None, "valid", strict=False)
                text = "\n".join(str(w.message) for w in caught)
                if name == "b_permuted":
                    self.assertFalse(caught)
                    self.assertEqual(baseline_metrics, result)
                    continue
                self.assertTrue(caught, "expected lenient warnings")
                self.assertGreater(result[TASK]["submission_warnings"], 0)
                self.assertIn("preferred_typed_mrr", result[TASK])
                if name == "a_id_swap":
                    self.assertIn("[off_pool_target]", text)
                    self.assertIn("[incomplete_query]", text)
                    self.assertGreater(loaded.diagnostics.filled_pairs, 0)
                if name == "c_dropped_row":
                    self.assertEqual(1, loaded.diagnostics.filled_pairs)
                if name == "d_renamed_id":
                    self.assertIn("NCIT-DOID-00c0ffee", text)
                    self.assertEqual(3, loaded.diagnostics.queries_scored)
                    self.assertEqual(3.0, result[TASK]["queries_scored"])
                if name == "e_duplicate_row":
                    self.assertEqual(1, loaded.diagnostics.duplicate_pairs)

    def test_unattributable_violations_are_counted(self) -> None:
        # a complete submission plus a malformed row, an ID with no task prefix and an
        # ID of a task that is not being scored: no task owns them, the macro reports them
        lines = self.baseline.read_text(encoding="utf-8").splitlines()
        extra = ["only\tthree\tfields", "BOGUS\tX\tY\tequivalent\t0.5", "SNOMED-FMA-deadbeef\tX\tY\tequivalent\t0.5"]
        path = self.baseline.with_name("unattributable.tsv")
        path.write_text("\n".join(lines + extra) + "\n", encoding="utf-8")
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            loaded = load_submission(path, self.index, strict=False)
            result = score_submission(path, MINI_PAIRED, None, "valid", strict=False)
        self.assertTrue(caught)
        self.assertEqual(3, loaded.diagnostics.unattributed_violations)
        self.assertEqual(0.0, result[TASK]["submission_warnings"])
        self.assertEqual(3.0, result["macro"]["submission_warnings_unattributed"])
        self.assertEqual(3.0, result["macro"]["submission_warnings_total"])
        with self.assertRaises(SubmissionError):
            score_submission(path, MINI_PAIRED, None, "valid", strict=True)

    def test_validate_submission_and_strict_flag_reject_the_swap(self) -> None:
        swapped = self._write("swap.tsv", self._id_swapped())
        self.assertTrue(validate_submission(swapped, [_cands(MINI_PAIRED)]).errors)


class LenientSemanticsTest(unittest.TestCase):
    def test_query_with_all_rows_dropped_is_skipped_not_ranked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_release(root, {"AB-CD": 2})
            rows = _perfect_rows(root, ["AB-CD"])
            victim = rows[0]["QueryID"]
            rows = [{**r, "Relation": "bogus"} if r["QueryID"] == victim else r for r in rows]
            path = root / "sub.tsv"
            _write_rows(path, rows)
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                result = score_submission(path, root, None, "valid", strict=False)
            self.assertEqual(1.0, result["AB-CD"]["queries_scored"])
            self.assertEqual(2.0, result["AB-CD"]["queries_expected"])
            self.assertEqual(1.0, result["AB-CD"]["preferred_typed_mrr"])

    def test_missing_pairs_are_filled_with_minus_infinity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_release(root, {"AB-CD": 1})
            rows = [r for r in _perfect_rows(root, ["AB-CD"]) if float(r["Score"]) > 0]
            index = load_query_index([root / "tasks" / "AB-CD" / "valid.cands.tsv"])
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                path = root / "sub.tsv"
                _write_rows(path, rows)
                loaded = load_submission(path, index, strict=False)
            scores = [row["Score"] for row in loaded.predictions_by_query["AB-CD-00000000"]]
            self.assertEqual(9, len(scores))
            self.assertEqual(8, sum(1 for s in scores if s == float("-inf")))
            self.assertEqual(8, loaded.diagnostics.filled_pairs)

    def test_two_task_macro_counts_and_rates(self) -> None:
        """10/10 queries of task A scored, 0/90 of task B."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_release(root, {"AA-BB": 10, "CC-DD": 90})
            path = root / "sub.tsv"
            _write_rows(path, _perfect_rows(root, ["AA-BB"]))
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                result = score_submission(path, root, None, "valid", strict=False)
            self.assertEqual({"queries_expected": 90.0, "queries_scored": 0.0, "submission_warnings": 90.0}, result["CC-DD"])
            macro = result["macro"]
            self.assertEqual(100.0, macro["queries_expected"])
            self.assertEqual(10.0, macro["queries_scored"])
            self.assertEqual(10.0, macro["queries"])
            self.assertEqual(1.0, macro["tasks_scored"])
            self.assertEqual(2.0, macro["tasks"])
            self.assertEqual(1.0, macro["preferred_typed_mrr"])
            with self.assertRaises(SubmissionError):
                score_submission(path, root, None, "valid", strict=True)

    def test_zero_scored_queries_raises_even_when_lenient(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_release(root, {"AB-CD": 2})
            path = root / "sub.tsv"
            _write_rows(path, [{**r, "QueryID": "AB-CD-ffffffff"} for r in _perfect_rows(root, ["AB-CD"])])
            with warnings.catch_warnings(record=True):
                warnings.simplefilter("always")
                with self.assertRaises(SubmissionError):
                    score_submission(path, root, None, "valid", strict=False)


class MacroAverageTest(unittest.TestCase):
    def test_count_registry(self) -> None:
        for key in ("queries", "queries_expected", "queries_scored", "submission_warnings", "preferred_typed_queries",
                    "preferred_entity_relation_queries", "hierarchy_aware_typed_ndcg_at_10_queries",
                    "hierarchy_aware_typed_ndcg_at_10__equivalence_only_queries", "datalog_inconsistency_count"):
            self.assertTrue(is_count_metric(key), key)
        for key in ("preferred_typed_mrr", "median_preferred_typed_rank", "hierarchy_aware_typed_ndcg_at_10", "datalog_conflict_free"):
            self.assertFalse(is_count_metric(key), key)

    def test_unequal_task_sizes(self) -> None:
        macro = macro_average_tasks({
            "A": {"preferred_typed_mrr": 0.5, "queries": 10.0, "preferred_typed_queries": 10.0},
            "B": {"preferred_typed_mrr": 0.1, "queries": 90.0, "preferred_typed_queries": 90.0},
        })
        self.assertAlmostEqual(0.3, macro["preferred_typed_mrr"])  # unweighted mean of tasks
        self.assertEqual(100.0, macro["queries"])
        self.assertEqual(100.0, macro["queries_sum"])
        self.assertEqual(50.0, macro["queries_mean"])
        self.assertEqual(100.0, macro["preferred_typed_queries_sum"])
        self.assertEqual(2.0, macro["tasks_scored"])


class EvaluationSetValidatorTest(unittest.TestCase):
    """The shared validator: planted violations fail both validate_evaluation_set
    and score_task."""

    def _plant(self, edit) -> tuple[list[str], Path, Path]:
        tmp = Path(tempfile.mkdtemp(prefix="kit-eval-"))
        _build_release(tmp, {"AB-CD": 4})
        sub = tmp / "sub.tsv"
        _write_rows(sub, _perfect_rows(tmp, ["AB-CD"]))
        edit(tmp / "evaluation")
        ev = tmp / "evaluation"
        index = load_query_index([ev / "AB-CD" / "valid.answers.tsv"])
        errors = validate_evaluation_set(
            index,
            load_answers(ev / "AB-CD" / "valid.answers.tsv"),
            load_preferred_pairs(ev / "AB-CD" / "valid.preferred.tsv"),
            load_graded_relevance(ev / "AB-CD" / "valid.graded.tsv"),
            load_query_metadata(ev / "query_metadata.tsv"),
        )
        return errors, tmp, sub

    @staticmethod
    def _edit_tsv(path: Path, change) -> None:
        rows = read_tsv(path)
        header = path.read_text().splitlines()[0].split("\t")
        write_tsv(path, change(rows), header)

    def test_clean_release_validates(self) -> None:
        errors, root, sub = self._plant(lambda ev: None)
        self.assertEqual([], errors)
        self.assertIn("AB-CD", score_submission(sub, root, None, "valid"))

    def test_planted_violations(self) -> None:
        cases = {
            "graded_off_pool": lambda ev: self._edit_tsv(ev / "AB-CD" / "valid.graded.tsv", lambda rows: rows + [{**rows[0], "TgtEntity": "T:elsewhere", "Gain": "0.5"}]),
            "graded_bad_relation": lambda ev: self._edit_tsv(ev / "AB-CD" / "valid.graded.tsv", lambda rows: rows + [{**rows[0], "Relation": "related_to", "Gain": "0.5"}]),
            "missing_preferred": lambda ev: self._edit_tsv(ev / "AB-CD" / "valid.preferred.tsv", lambda rows: rows[1:]),
            "preferred_gain_not_one": lambda ev: self._edit_tsv(ev / "AB-CD" / "valid.graded.tsv", lambda rows: [{**rows[0], "Gain": "0.6"}] + rows[1:]),
            "graded_bad_gain": lambda ev: self._edit_tsv(ev / "AB-CD" / "valid.graded.tsv", lambda rows: rows + [{**rows[0], "TgtEntity": sorted(parse_list(read_tsv(ev / "AB-CD" / "valid.answers.tsv")[0]["TgtCandidates"]))[1], "Gain": "1.5"}]),
            "metadata_mode_mismatch": lambda ev: self._edit_tsv(ev / "query_metadata.tsv", lambda rows: [{**rows[0], "QueryMode": "subsumption"}] + rows[1:]),
            "missing_metadata": lambda ev: self._edit_tsv(ev / "query_metadata.tsv", lambda rows: rows[1:]),
            "preferred_not_gold": lambda ev: self._edit_tsv(ev / "AB-CD" / "valid.preferred.tsv", lambda rows: [{**rows[0], "Relation": "source_subsumes_target"}] + rows[1:]),
        }
        for rule, edit in cases.items():
            with self.subTest(rule=rule):
                errors, root, sub = self._plant(edit)
                self.assertTrue(any(f"[{rule}]" in e for e in errors), errors)
                with self.assertRaises(EvaluationSetError):
                    score_task(load_submission(sub, load_query_index([root / "tasks" / "AB-CD" / "valid.cands.tsv"]), strict=False), root / "evaluation", "AB-CD", "valid")

    def test_loaders_reject_duplicates_and_bad_pools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _build_release(root, {"AB-CD": 2})
            ev = root / "evaluation" / "AB-CD"
            self._edit_tsv(ev / "valid.graded.tsv", lambda rows: rows + [rows[0]])
            with self.assertRaises(EvaluationSetError):
                load_graded_relevance(ev / "valid.graded.tsv")
            self._edit_tsv(ev / "valid.preferred.tsv", lambda rows: rows + [rows[0]])
            with self.assertRaises(EvaluationSetError):
                load_preferred_pairs(ev / "valid.preferred.tsv")
            cands = root / "tasks" / "AB-CD" / "valid.cands.tsv"
            for change in (
                lambda rows: rows + [rows[0]],                                          # duplicate ID
                lambda rows: [{**rows[0], "TgtCandidates": "[]"}] + rows[1:],           # empty pool
                lambda rows: [{**rows[0], "TgtCandidates": "['x', 'x']"}] + rows[1:],   # duplicate candidate
                lambda rows: [{**rows[0], "QueryID": "XY-ZW-00000000"}] + rows[1:],     # task prefix != directory
                lambda rows: [{**rows[0], "QueryID": "Q0"}] + rows[1:],                 # legacy ID
            ):
                original = cands.read_text()
                self._edit_tsv(cands, change)
                with self.assertRaises(EvaluationSetError):
                    load_query_index([cands])
                cands.write_text(original)


class CliTest(unittest.TestCase):
    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "biokg_align_kit", *args], capture_output=True, text=True,
            env={"PYTHONPATH": str(REPO_ROOT / "src"), "PATH": "/usr/bin:/bin"},
        )

    def test_score_is_lenient_by_default_and_strict_on_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "p.tsv"
            predict(MINI_PAIRED, TASK, "valid", "hybrid_lexical", predictions)
            rows = read_tsv(predictions)
            _write_rows(predictions, rows[1:])
            lenient = self._run("score", "--predictions", str(predictions), "--data-dir", str(MINI_PAIRED), "--split", "valid")
            self.assertEqual(0, lenient.returncode, lenient.stderr)
            self.assertIn("NOT leaderboard-comparable", lenient.stderr)
            strict = self._run("score", "--predictions", str(predictions), "--data-dir", str(MINI_PAIRED), "--split", "valid", "--strict")
            self.assertEqual(1, strict.returncode)
            verify = self._run("verify", "--predictions", str(predictions), "--data-dir", str(MINI_PAIRED), "--split", "valid")
            self.assertEqual(1, verify.returncode)
            self.assertIn("[incomplete_query]", verify.stderr)

    def test_unattributed_warnings_through_both_score_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "p.tsv"
            predict(MINI_PAIRED, TASK, "valid", "hybrid_lexical", predictions)
            with predictions.open("a", encoding="utf-8") as handle:
                handle.write("BOGUS\tX\tY\tequivalent\t0.5\n")
            answers = MINI_PAIRED / "evaluation" / TASK / "valid.answers.tsv"
            for extra in (["--data-dir", str(MINI_PAIRED), "--split", "valid"], ["--answers", str(answers)]):
                with self.subTest(path=extra[0]):
                    result = self._run("score", "--predictions", str(predictions), *extra)
                    self.assertEqual(0, result.returncode, result.stderr)
                    self.assertIn("macro\tsubmission_warnings_unattributed\t1.000000", result.stdout)
                    self.assertIn("macro\tsubmission_warnings_total\t1.000000", result.stdout)

    def test_removed_block_era_options(self) -> None:
        for option in ("--candidate-count", "--submission-format"):
            result = self._run("score", "--predictions", "x", "--data-dir", "y", "--split", "valid", option, "1")
            self.assertEqual(2, result.returncode)
        result = self._run("verify", "--predictions", "x", "--data-dir", "y", "--candidates-per-query", "1")
        self.assertEqual(2, result.returncode)


if __name__ == "__main__":
    unittest.main()
