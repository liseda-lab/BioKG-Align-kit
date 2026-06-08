from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from biokg_align_kit.baselines import predict
from biokg_align_kit.scoring import score_files
from biokg_align_kit.validation import (
    DEFAULT_CANDIDATES_PER_QUERY,
    ValidationResult,
    validate_submission,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MINI = REPO_ROOT / "examples" / "mini"
CANONICAL = REPO_ROOT / "examples" / "canonical"


class KitTest(unittest.TestCase):
    def test_baseline_scores_and_validates(self) -> None:
        """End-to-end: predict, validate (lenient mode), and score."""
        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "predictions.tsv"
            predict(MINI, "NCIT-DOID", "valid", "hybrid_lexical", predictions)

            # The mini fixture has 3 candidates per query (not the
            # canonical 50), so disable the candidate-count check to
            # avoid spurious warnings in this end-to-end test.
            result = validate_submission(
                predictions,
                MINI / "tasks" / "NCIT-DOID" / "valid.cands.tsv",
                candidates_per_query=None,
            )
            self.assertIsInstance(result, ValidationResult)
            self.assertFalse(result.errors)
            self.assertFalse(result.warnings)

            metrics = score_files(
                predictions,
                MINI / "answers" / "NCIT-DOID.valid.answers.tsv",
            )
            # Renamed in P5: legacy 'relation_aware_ndcg_at_10' is now
            # 'diagnostic_relation_aware_ndcg_at_10' (leaderboard scores
            # use the preferred-pair family instead).
            self.assertIn("diagnostic_relation_aware_ndcg_at_10", metrics)
            # The mini fixture ships preferred + graded files (P6); both
            # headline metric families should be emitted.
            self.assertIn("preferred_typed_mrr", metrics)
            self.assertIn("hierarchy_aware_typed_ndcg_at_10", metrics)


class ValidationCandidateCountTest(unittest.TestCase):
    """Coverage for the block-format validator's candidate-count and
    missing-pair checks."""

    def test_default_50_errors_on_non_canonical_fixture(self) -> None:
        """
        The mini fixture has 4 candidates per query; when
        candidates_per_query defaults to 50, the block validator
        expects 2 * (50 * 3) = 300 rows but the submission has 24.
        This is a fatal row-count mismatch error.
        """
        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "predictions.tsv"
            predict(MINI, "NCIT-DOID", "valid", "hybrid_lexical", predictions)

            result = validate_submission(
                predictions,
                MINI / "tasks" / "NCIT-DOID" / "valid.cands.tsv",
                candidates_per_query=DEFAULT_CANDIDATES_PER_QUERY,
            )
            self.assertEqual(DEFAULT_CANDIDATES_PER_QUERY, 50)
            self.assertTrue(result.errors)
            self.assertTrue(
                any("row count mismatch" in e.lower() for e in result.errors),
                msg=f"Expected row-count mismatch error; got {result.errors}",
            )

    def test_disabled_candidate_check_no_warning(self) -> None:
        """``candidates_per_query=None`` derives per-block candidate
        counts from the cands file; a well-formed submission passes
        cleanly."""
        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "predictions.tsv"
            predict(MINI, "NCIT-DOID", "valid", "hybrid_lexical", predictions)

            result = validate_submission(
                predictions,
                MINI / "tasks" / "NCIT-DOID" / "valid.cands.tsv",
                candidates_per_query=None,
            )
            self.assertFalse(result.errors)
            self.assertFalse(result.warnings)

    def test_row_count_mismatch_is_error(self) -> None:
        """A submission with the wrong total row count is fatal."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            predictions = tmp_path / "predictions.tsv"
            predictions.write_text(
                "SrcEntity\tTgtEntity\tRelation\tScore\n"
                "NCIT:C001\tDOID:D001\tequivalent\t0.5\n"
            )
            result = validate_submission(
                predictions,
                MINI / "tasks" / "NCIT-DOID" / "valid.cands.tsv",
                candidates_per_query=None,
            )
            self.assertTrue(result.errors)
            self.assertTrue(
                any("row count mismatch" in e.lower() for e in result.errors)
            )


class TieBreakOrderTest(unittest.TestCase):
    """Coverage for explicit relation tie-break ordering (paper §2.1)."""

    def test_explicit_relation_order_when_score_and_target_tie(self) -> None:
        """
        With identical scores and target entities, the relation order
        must be: equivalent ≺ source_subsumed_by_target ≺
        source_subsumes_target — independent of lexicographic accident.
        """
        from biokg_align_kit.scoring import RELATION_TIEBREAK_ORDER, _rank_key

        self.assertEqual(
            RELATION_TIEBREAK_ORDER,
            {
                "equivalent": 0,
                "source_subsumed_by_target": 1,
                "source_subsumes_target": 2,
            },
        )

        rows = [
            {"TgtEntity": "DOID:X", "Relation": "source_subsumes_target",  "Score": "0.5"},
            {"TgtEntity": "DOID:X", "Relation": "equivalent",              "Score": "0.5"},
            {"TgtEntity": "DOID:X", "Relation": "source_subsumed_by_target","Score": "0.5"},
        ]
        ranked = sorted(rows, key=_rank_key)
        self.assertEqual(
            [r["Relation"] for r in ranked],
            ["equivalent", "source_subsumed_by_target", "source_subsumes_target"],
        )

    def test_target_breaks_tie_before_relation(self) -> None:
        """When scores tie but targets differ, TgtEntity sorts first."""
        from biokg_align_kit.scoring import _rank_key

        rows = [
            {"TgtEntity": "DOID:B", "Relation": "equivalent",              "Score": "0.5"},
            {"TgtEntity": "DOID:A", "Relation": "source_subsumes_target", "Score": "0.5"},
        ]
        ranked = sorted(rows, key=_rank_key)
        self.assertEqual([r["TgtEntity"] for r in ranked], ["DOID:A", "DOID:B"])

    def test_score_dominates_tiebreaks(self) -> None:
        """Higher score always wins regardless of target / relation."""
        from biokg_align_kit.scoring import _rank_key

        rows = [
            {"TgtEntity": "DOID:A", "Relation": "equivalent",              "Score": "0.1"},
            {"TgtEntity": "DOID:Z", "Relation": "source_subsumes_target", "Score": "0.9"},
        ]
        ranked = sorted(rows, key=_rank_key)
        self.assertEqual([r["TgtEntity"] for r in ranked], ["DOID:Z", "DOID:A"])


class PreferredPairMetricsTest(unittest.TestCase):
    """Coverage for the preferred-pair metric family (paper §1.5)."""

    @staticmethod
    def _write_preferred(path: Path, pairs: list[tuple[str, str, str]]) -> None:
        """Write a preferred-pair TSV with rows ``(src, tgt, relation)``."""
        with path.open("w", encoding="utf-8") as handle:
            handle.write("SrcEntity\tTgtEntity\tRelation\n")
            for src, tgt, rel in pairs:
                handle.write(f"{src}\t{tgt}\t{rel}\n")

    @staticmethod
    def _write_predictions(
        path: Path, rows: list[tuple[str, str, str, float]]
    ) -> None:
        """Write a predictions TSV with rows ``(src, tgt, relation, score)``."""
        with path.open("w", encoding="utf-8") as handle:
            handle.write("SrcEntity\tTgtEntity\tRelation\tScore\n")
            for src, tgt, rel, score in rows:
                handle.write(f"{src}\t{tgt}\t{rel}\t{score}\n")

    def test_load_preferred_pairs_round_trip(self) -> None:
        from biokg_align_kit.scoring import load_preferred_pairs

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "preferred.tsv"
            self._write_preferred(path, [
                ("NCIT:C001", "DOID:D001", "equivalent"),
                ("NCIT:C002", "DOID:D002", "source_subsumed_by_target"),
            ])
            loaded = load_preferred_pairs(path)
            # v0.1.3: keys are (SrcEntity, QueryID) tuples. The legacy
            # 3-column preferred TSV without QueryID falls back to
            # "Q0" for every row.
            self.assertEqual(loaded, {
                ("NCIT:C001", "Q0"): ("DOID:D001", "equivalent"),
                ("NCIT:C002", "Q0"): ("DOID:D002", "source_subsumed_by_target"),
            })

    def test_preferred_pair_hit_at_rank_1(self) -> None:
        """
        When the preferred pair is ranked first, MRR = Hits@1 = 1.0
        for that query.
        """
        from biokg_align_kit.scoring import load_preferred_pairs, score_files

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            answers = tmp_path / "task.valid.answers.tsv"
            answers.write_text(
                "SrcEntity\tTgtEntities\tRelations\tTgtCandidates\n"
                "S1\t['T1']\t['equivalent']\t['T1', 'T2']\n"
            )
            preferred = tmp_path / "task.valid.preferred.tsv"
            self._write_preferred(preferred, [("S1", "T1", "equivalent")])
            preds = tmp_path / "preds.tsv"
            # (T1, equivalent) is the top-ranked pair.
            self._write_predictions(preds, [
                ("S1", "T1", "equivalent",                 0.9),
                ("S1", "T1", "source_subsumed_by_target",  0.5),
                ("S1", "T1", "source_subsumes_target",     0.4),
                ("S1", "T2", "equivalent",                 0.3),
                ("S1", "T2", "source_subsumed_by_target",  0.2),
                ("S1", "T2", "source_subsumes_target",     0.1),
            ])
            metrics = score_files(preds, answers, submission_format="row")
            self.assertEqual(metrics["preferred_typed_mrr"], 1.0)
            self.assertEqual(metrics["preferred_typed_hits_at_1"], 1.0)
            self.assertEqual(metrics["preferred_typed_hits_at_5"], 1.0)
            self.assertEqual(metrics["preferred_typed_hits_at_10"], 1.0)
            self.assertEqual(metrics["preferred_typed_queries"], 1.0)

    def test_preferred_pair_hit_at_rank_3(self) -> None:
        """MRR = 1/3 when preferred is ranked third."""
        from biokg_align_kit.scoring import score_files

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            answers = tmp_path / "task.valid.answers.tsv"
            answers.write_text(
                "SrcEntity\tTgtEntities\tRelations\tTgtCandidates\n"
                "S1\t['T1']\t['equivalent']\t['T1', 'T2']\n"
            )
            preferred = tmp_path / "task.valid.preferred.tsv"
            self._write_preferred(preferred, [("S1", "T1", "equivalent")])
            preds = tmp_path / "preds.tsv"
            # (T1, equivalent) ranked third: two rows score higher.
            self._write_predictions(preds, [
                ("S1", "T2", "equivalent",                 0.9),  # rank 1
                ("S1", "T2", "source_subsumed_by_target",  0.8),  # rank 2
                ("S1", "T1", "equivalent",                 0.7),  # rank 3  <- preferred
                ("S1", "T1", "source_subsumed_by_target",  0.6),
                ("S1", "T1", "source_subsumes_target",     0.5),
                ("S1", "T2", "source_subsumes_target",     0.4),
            ])
            metrics = score_files(preds, answers, submission_format="row")
            self.assertAlmostEqual(metrics["preferred_typed_mrr"], 1.0 / 3.0)
            self.assertEqual(metrics["preferred_typed_hits_at_1"], 0.0)
            self.assertEqual(metrics["preferred_typed_hits_at_5"], 1.0)

    def test_preferred_pair_miss(self) -> None:
        """If the preferred pair is absent from predictions, MRR = 0."""
        from biokg_align_kit.scoring import score_files

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            answers = tmp_path / "task.valid.answers.tsv"
            answers.write_text(
                "SrcEntity\tTgtEntities\tRelations\tTgtCandidates\n"
                "S1\t['T1']\t['equivalent']\t['T1', 'T2']\n"
            )
            preferred = tmp_path / "task.valid.preferred.tsv"
            self._write_preferred(preferred, [("S1", "T1", "equivalent")])
            preds = tmp_path / "preds.tsv"
            # Predictions never include (T1, equivalent).
            self._write_predictions(preds, [
                ("S1", "T1", "source_subsumed_by_target",  0.5),
                ("S1", "T1", "source_subsumes_target",     0.4),
                ("S1", "T2", "equivalent",                 0.3),
            ])
            metrics = score_files(preds, answers, submission_format="row")
            self.assertEqual(metrics["preferred_typed_mrr"], 0.0)
            self.assertEqual(metrics["preferred_typed_hits_at_1"], 0.0)
            self.assertEqual(metrics["preferred_typed_hits_at_10"], 0.0)

    def test_macro_across_two_queries(self) -> None:
        """Macro MRR averages query-level RRs."""
        from biokg_align_kit.scoring import score_files

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            answers = tmp_path / "task.valid.answers.tsv"
            answers.write_text(
                "SrcEntity\tTgtEntities\tRelations\tTgtCandidates\n"
                "S1\t['T1']\t['equivalent']\t['T1', 'T2']\n"
                "S2\t['T3']\t['equivalent']\t['T3', 'T4']\n"
            )
            preferred = tmp_path / "task.valid.preferred.tsv"
            self._write_preferred(preferred, [
                ("S1", "T1", "equivalent"),
                ("S2", "T3", "equivalent"),
            ])
            preds = tmp_path / "preds.tsv"
            self._write_predictions(preds, [
                # S1: preferred at rank 1 (RR = 1.0)
                ("S1", "T1", "equivalent",                 0.9),
                ("S1", "T2", "equivalent",                 0.5),
                # S2: preferred at rank 2 (RR = 0.5)
                ("S2", "T4", "equivalent",                 0.8),
                ("S2", "T3", "equivalent",                 0.6),
            ])
            metrics = score_files(preds, answers, submission_format="row")
            self.assertAlmostEqual(metrics["preferred_typed_mrr"], 0.75)
            self.assertEqual(metrics["preferred_typed_queries"], 2.0)

    def test_partial_coverage_skips_uncovered_queries(self) -> None:
        """
        When the preferred file omits a query, that query is dropped
        from preferred-pair metrics but still counted in the diagnostic
        family. This matches the organizer-side behaviour for queries
        with no equivalent gold.
        """
        from biokg_align_kit.scoring import score_files

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            answers = tmp_path / "task.valid.answers.tsv"
            answers.write_text(
                "SrcEntity\tTgtEntities\tRelations\tTgtCandidates\n"
                "S1\t['T1']\t['equivalent']\t['T1', 'T2']\n"
                "S2\t['T3']\t['source_subsumed_by_target']\t['T3', 'T4']\n"
            )
            preferred = tmp_path / "task.valid.preferred.tsv"
            # S2 omitted (e.g. no equivalent gold, skipped organizer-side).
            self._write_preferred(preferred, [("S1", "T1", "equivalent")])
            preds = tmp_path / "preds.tsv"
            self._write_predictions(preds, [
                ("S1", "T1", "equivalent", 0.9),
                ("S1", "T2", "equivalent", 0.3),
                ("S2", "T3", "source_subsumed_by_target", 0.9),
                ("S2", "T4", "equivalent", 0.3),
            ])
            metrics = score_files(preds, answers, submission_format="row")
            self.assertEqual(metrics["queries"], 2.0)
            self.assertEqual(metrics["preferred_typed_queries"], 1.0)
            self.assertEqual(metrics["preferred_typed_mrr"], 1.0)

    def test_missing_preferred_file_skips_metric_with_stderr_note(self) -> None:
        """
        Backward compatibility: if no *.preferred.tsv exists, the
        scorer emits a stderr note and reports only diagnostic metrics.
        """
        import io
        import contextlib

        from biokg_align_kit.scoring import score_files

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            answers = tmp_path / "task.valid.answers.tsv"
            answers.write_text(
                "SrcEntity\tTgtEntities\tRelations\tTgtCandidates\n"
                "S1\t['T1']\t['equivalent']\t['T1', 'T2']\n"
            )
            preds = tmp_path / "preds.tsv"
            self._write_predictions(preds, [
                ("S1", "T1", "equivalent", 0.9),
                ("S1", "T2", "equivalent", 0.3),
            ])

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                metrics = score_files(preds, answers, submission_format="row")
            self.assertIn("preferred-pair metrics skipped", stderr.getvalue())
            self.assertNotIn("preferred_typed_mrr", metrics)
            self.assertIn("diagnostic_relation_aware_ndcg_at_10", metrics)

    def test_explicit_preferred_path_overrides_convention(self) -> None:
        """``preferred_path=`` argument is respected when given."""
        from biokg_align_kit.scoring import score_files

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            answers = tmp_path / "task.valid.answers.tsv"
            answers.write_text(
                "SrcEntity\tTgtEntities\tRelations\tTgtCandidates\n"
                "S1\t['T1']\t['equivalent']\t['T1', 'T2']\n"
            )
            # File at a non-conventional path.
            preferred = tmp_path / "elsewhere" / "my.preferred.tsv"
            preferred.parent.mkdir()
            self._write_preferred(preferred, [("S1", "T1", "equivalent")])
            preds = tmp_path / "preds.tsv"
            self._write_predictions(preds, [
                ("S1", "T1", "equivalent", 0.9),
                ("S1", "T2", "equivalent", 0.3),
            ])
            metrics = score_files(preds, answers, preferred_path=preferred, submission_format="row")
            self.assertIn("preferred_typed_mrr", metrics)
            self.assertEqual(metrics["preferred_typed_mrr"], 1.0)


class PreferredEntityRelationMacroF1Test(unittest.TestCase):
    """
    Coverage for the paper-faithful Relation Macro-F1 on the Preferred
    Entity diagnostic (paper §1.5). The metric is conditioned on
    entity-correctness: only queries where the system's top-ranked
    entity (max score across relation types) matches the preferred
    gold entity contribute to the macro-F1 calculation.
    """

    @staticmethod
    def _write_preferred(path: Path, pairs: list[tuple[str, str, str]]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            handle.write("SrcEntity\tTgtEntity\tRelation\n")
            for src, tgt, rel in pairs:
                handle.write(f"{src}\t{tgt}\t{rel}\n")

    @staticmethod
    def _write_predictions(
        path: Path, rows: list[tuple[str, str, str, float]]
    ) -> None:
        with path.open("w", encoding="utf-8") as handle:
            handle.write("SrcEntity\tTgtEntity\tRelation\tScore\n")
            for src, tgt, rel, score in rows:
                handle.write(f"{src}\t{tgt}\t{rel}\t{score}\n")

    @staticmethod
    def _write_answers(path: Path, rows: list[tuple[str, list[str], list[str]]]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            handle.write("SrcEntity\tTgtEntities\tRelations\tTgtCandidates\n")
            for src, tgts, rels in rows:
                tgts_str = "[" + ", ".join(f"'{t}'" for t in tgts) + "]"
                rels_str = "[" + ", ".join(f"'{r}'" for r in rels) + "]"
                handle.write(f"{src}\t{tgts_str}\t{rels_str}\t{tgts_str}\n")

    def test_correct_entity_correct_relation_scores_one(self) -> None:
        """System top-1 matches preferred (entity, relation) → F1=1.0."""
        from biokg_align_kit.scoring import score_files

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            answers = tmp_path / "t.valid.answers.tsv"
            self._write_answers(answers, [("S1", ["T1"], ["equivalent"])])
            self._write_preferred(
                tmp_path / "t.valid.preferred.tsv",
                [("S1", "T1", "equivalent")],
            )
            preds = tmp_path / "preds.tsv"
            self._write_predictions(preds, [
                ("S1", "T1", "equivalent",                 0.9),  # top
                ("S1", "T1", "source_subsumed_by_target",  0.5),
                ("S1", "T2", "equivalent",                 0.3),
            ])
            metrics = score_files(preds, answers, submission_format="row")
            self.assertEqual(metrics["preferred_entity_relation_accuracy"], 1.0)
            self.assertEqual(metrics["preferred_entity_relation_macro_f1"], 1.0)
            self.assertEqual(metrics["preferred_entity_relation_queries"], 1.0)

    def test_wrong_entity_excludes_query(self) -> None:
        """
        System's top entity is wrong → query is dropped from the
        preferred-entity macro-F1 family. Macro-F1 reports 0.0 with
        coverage count 0 — the metric is unmeasurable, not zero.
        """
        from biokg_align_kit.scoring import score_files

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            answers = tmp_path / "t.valid.answers.tsv"
            self._write_answers(answers, [("S1", ["T1"], ["equivalent"])])
            self._write_preferred(
                tmp_path / "t.valid.preferred.tsv",
                [("S1", "T1", "equivalent")],
            )
            preds = tmp_path / "preds.tsv"
            # Top entity is T2 (max score 0.9), but preferred is T1.
            # Query is excluded from the macro-F1 family.
            self._write_predictions(preds, [
                ("S1", "T2", "equivalent", 0.9),
                ("S1", "T1", "equivalent", 0.3),
            ])
            metrics = score_files(preds, answers, submission_format="row")
            self.assertEqual(metrics["preferred_entity_relation_queries"], 0.0)
            self.assertEqual(metrics["preferred_entity_relation_macro_f1"], 0.0)
            self.assertEqual(metrics["preferred_entity_relation_accuracy"], 0.0)

    def test_correct_entity_wrong_relation_macro_f1(self) -> None:
        """
        Correct entity but wrong relation: accuracy=0, macro-F1 reflects
        the misclassification. Verifies the metric isn't just a copy
        of accuracy.
        """
        from biokg_align_kit.scoring import score_files

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            answers = tmp_path / "t.valid.answers.tsv"
            self._write_answers(answers, [("S1", ["T1"], ["equivalent"])])
            self._write_preferred(
                tmp_path / "t.valid.preferred.tsv",
                [("S1", "T1", "equivalent")],
            )
            preds = tmp_path / "preds.tsv"
            # Top entity correct (T1), but top relation is ssbt not equivalent.
            self._write_predictions(preds, [
                ("S1", "T1", "source_subsumed_by_target", 0.9),  # top — wrong relation
                ("S1", "T1", "equivalent",                0.5),
            ])
            metrics = score_files(preds, answers, submission_format="row")
            self.assertEqual(metrics["preferred_entity_relation_queries"], 1.0)
            self.assertEqual(metrics["preferred_entity_relation_accuracy"], 0.0)
            # macro-F1 averages across the relations present in the
            # confusion: ssbt has 1 FP (P=R=F1=0), equivalent has 1 FN
            # (P=R=F1=0); third relation absent. Mean over 2 relations
            # = 0.0.
            self.assertEqual(metrics["preferred_entity_relation_macro_f1"], 0.0)

    def test_collapse_across_relations_picks_max_score(self) -> None:
        """
        Top entity selection should collapse scores across relation
        types by max, not sum or mean. Verifies the entity-only ranking
        idiom matches the paper's "max across relation types" wording.
        """
        from biokg_align_kit.scoring import score_files

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            answers = tmp_path / "t.valid.answers.tsv"
            self._write_answers(answers, [("S1", ["T1"], ["equivalent"])])
            self._write_preferred(
                tmp_path / "t.valid.preferred.tsv",
                [("S1", "T1", "equivalent")],
            )
            preds = tmp_path / "preds.tsv"
            # T2's per-row scores sum to a higher number than T1's, but
            # T1 has the highest single-row score (0.95). Max-collapse
            # should pick T1 as the top entity; the query should
            # contribute (correctly) to the macro-F1 family.
            self._write_predictions(preds, [
                ("S1", "T1", "equivalent",                 0.95),  # top entity
                ("S1", "T1", "source_subsumed_by_target",  0.0),
                ("S1", "T2", "equivalent",                 0.7),
                ("S1", "T2", "source_subsumed_by_target",  0.7),
                ("S1", "T2", "source_subsumes_target",     0.7),
            ])
            metrics = score_files(preds, answers, submission_format="row")
            self.assertEqual(metrics["preferred_entity_relation_queries"], 1.0)
            self.assertEqual(metrics["preferred_entity_relation_accuracy"], 1.0)

    def test_macro_over_multiple_queries_and_relations(self) -> None:
        """
        Three entity-correct queries with mixed correctness across two
        relation types. Hand-computed reference:

        - Q1: preferred (T1, equivalent),  predicted (T1, equivalent)  → TP[eq]+=1
        - Q2: preferred (T1, ssbt),        predicted (T1, ssbt)        → TP[ssbt]+=1
        - Q3: preferred (T1, equivalent),  predicted (T1, ssbt)        → FP[ssbt]+=1, FN[eq]+=1

        equivalent: TP=1, FP=0, FN=1 → P=1.0, R=0.5, F1 = 2/3
        ssbt:       TP=1, FP=1, FN=0 → P=0.5, R=1.0, F1 = 2/3

        macro-F1 = mean(2/3, 2/3) = 2/3 ≈ 0.667
        accuracy = 2/3 (two of three queries got the relation right)
        """
        from biokg_align_kit.scoring import score_files

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            answers = tmp_path / "t.valid.answers.tsv"
            self._write_answers(answers, [
                ("S1", ["T1"], ["equivalent"]),
                ("S2", ["T1"], ["source_subsumed_by_target"]),
                ("S3", ["T1"], ["equivalent"]),
            ])
            self._write_preferred(
                tmp_path / "t.valid.preferred.tsv",
                [
                    ("S1", "T1", "equivalent"),
                    ("S2", "T1", "source_subsumed_by_target"),
                    ("S3", "T1", "equivalent"),
                ],
            )
            preds = tmp_path / "preds.tsv"
            self._write_predictions(preds, [
                # Q1: correct entity, correct relation
                ("S1", "T1", "equivalent",                 0.9),
                # Q2: correct entity, correct relation
                ("S2", "T1", "source_subsumed_by_target",  0.9),
                # Q3: correct entity, wrong relation
                ("S3", "T1", "source_subsumed_by_target",  0.9),
                ("S3", "T1", "equivalent",                 0.5),
            ])
            metrics = score_files(preds, answers, submission_format="row")
            self.assertEqual(metrics["preferred_entity_relation_queries"], 3.0)
            self.assertAlmostEqual(metrics["preferred_entity_relation_accuracy"], 2.0 / 3.0)
            self.assertAlmostEqual(metrics["preferred_entity_relation_macro_f1"], 2.0 / 3.0)

    def test_metric_absent_when_preferred_file_absent(self) -> None:
        """Without preferred pairs, the new metric keys must not appear."""
        from biokg_align_kit.scoring import score_files
        import io
        import contextlib

        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            answers = tmp_path / "t.valid.answers.tsv"
            self._write_answers(answers, [("S1", ["T1"], ["equivalent"])])
            preds = tmp_path / "preds.tsv"
            self._write_predictions(preds, [("S1", "T1", "equivalent", 0.9)])

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                metrics = score_files(preds, answers, submission_format="row")
            self.assertNotIn("preferred_entity_relation_macro_f1", metrics)
            self.assertNotIn("preferred_entity_relation_accuracy", metrics)
            self.assertNotIn("preferred_entity_relation_queries", metrics)


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


class BuildGradedRelevanceCliTest(unittest.TestCase):
    """Smoke test for the build-graded-relevance CLI subcommand."""

    def test_generates_graded_file_matching_mini_fixture(self) -> None:
        """Run the helper and confirm it produces deterministic output
        identical to the committed mini graded file."""
        import subprocess

        # Compare against the committed mini graded file produced by the
        # same helper. If the helper logic changes, this test will catch
        # it (and the committed fixture should be regenerated).
        committed = (MINI / "answers" / "NCIT-DOID.valid.graded.tsv").read_text()

        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "regen.graded.tsv"
            result = subprocess.run(
                [
                    sys.executable, "-m", "biokg_align_kit",
                    "build-graded-relevance",
                    "--preferred", str(MINI / "answers" / "NCIT-DOID.valid.preferred.tsv"),
                    "--candidates", str(MINI / "tasks" / "NCIT-DOID" / "valid.cands.tsv"),
                    "--triples", str(MINI / "graph" / "triples.csv"),
                    "--output", str(output),
                ],
                env={
                    "PYTHONPATH": str(REPO_ROOT / "src"),
                    "PATH": "/usr/bin:/bin",
                },
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                result.returncode, 0,
                msg=f"build-graded-relevance failed: stdout={result.stdout!r}, stderr={result.stderr!r}",
            )
            self.assertEqual(output.read_text(), committed)


import sys  # for BuildGradedRelevanceCliTest


class BaselineNamingTest(unittest.TestCase):
    """
    Coverage for the P2 rename: 'lexical' → 'hybrid_lexical' to match
    paper §1.6 Table 4. The old name raises a pointed ValueError that
    tells the caller what changed.
    """

    def test_legacy_lexical_name_raises_helpful_error(self) -> None:
        """
        Calling score() with the old name surfaces a clear error
        message identifying the rename and the new name, rather than
        silently doing the wrong thing or falling through to the
        generic 'Unsupported baseline' branch.
        """
        from biokg_align_kit.baselines import score

        with self.assertRaises(ValueError) as ctx:
            score("s", "t", "equivalent", {}, "lexical", seed=17)
        message = str(ctx.exception)
        self.assertIn("hybrid_lexical", message)
        self.assertIn("renamed", message.lower())

    def test_hybrid_lexical_runs_end_to_end(self) -> None:
        """The new name works against the mini fixture."""
        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "predictions.tsv"
            predict(MINI, "NCIT-DOID", "valid", "hybrid_lexical", predictions)
            self.assertTrue(predictions.exists())


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


class PairedQueryKeyingTest(unittest.TestCase):
    """
    Regression coverage for the v0.1.3 per-``(SrcEntity, QueryID)``
    keying contract.

    Under the canonical pool model, the same ``SrcEntity`` contributes
    a Q0 (equivalence) and a Q1 (subsumption) query — same source,
    distinct preferred pairs, distinct candidate pools allowed in
    principle though usually shared.

    Pre-v0.1.3 kit code keyed answers / preferred pairs / graded
    relevance by ``SrcEntity`` alone, which silently collapsed Q0 and
    Q1 into a single per-source entry — the Q1 row overwrote the Q0
    row (or vice versa) and one of the two queries was lost from every
    metric family. The tests below pin this down: the mini_paired
    fixture has 4 queries (2 sources × 2 queries); the kit must
    report 4 in every ``*_queries`` accumulator under the headline
    families that count queries.
    """

    MINI_PAIRED = REPO_ROOT / "examples" / "mini_paired"

    def test_load_preferred_pairs_keeps_both_modes_per_source(self) -> None:
        from biokg_align_kit.scoring import load_preferred_pairs

        loaded = load_preferred_pairs(
            self.MINI_PAIRED / "answers" / "NCIT-DOID.valid.preferred.tsv"
        )
        # 4 preferred-pair rows in → 4 entries out, keyed by
        # (SrcEntity, QueryID). A per-SrcEntity-only loader would
        # collapse to 2 here, with Q1 silently overwriting Q0.
        self.assertEqual(len(loaded), 4)
        self.assertEqual(
            loaded[("NCIT:C001", "Q0")], ("DOID:D001", "equivalent")
        )
        self.assertEqual(
            loaded[("NCIT:C001", "Q1")],
            ("DOID:D000", "source_subsumed_by_target"),
        )
        self.assertEqual(
            loaded[("NCIT:C002", "Q0")], ("DOID:D002", "equivalent")
        )
        self.assertEqual(
            loaded[("NCIT:C002", "Q1")],
            ("DOID:D000", "source_subsumed_by_target"),
        )

    def test_load_answers_keeps_both_modes_per_source(self) -> None:
        from biokg_align_kit.scoring import load_answers

        loaded = load_answers(
            self.MINI_PAIRED / "answers" / "NCIT-DOID.valid.answers.tsv"
        )
        # 4 queries in the answers file → 4 gold sets out. The
        # historical pre-v0.1.3 bug: keying by SrcEntity merged Q0 +
        # Q1 gold sets into one per-source set, which both
        # double-counted (set union) and lost the per-query
        # cardinality every metric needs.
        self.assertEqual(len(loaded), 4)
        self.assertEqual(
            loaded[("NCIT:C001", "Q0")], {("DOID:D001", "equivalent")}
        )
        self.assertEqual(
            loaded[("NCIT:C001", "Q1")],
            {("DOID:D000", "source_subsumed_by_target")},
        )

    def test_load_graded_relevance_keeps_both_modes_per_source(self) -> None:
        from biokg_align_kit.hierarchy import load_graded_relevance

        gains = load_graded_relevance(
            self.MINI_PAIRED / "answers" / "NCIT-DOID.valid.graded.tsv"
        )
        # Both (NCIT:C001, Q0) and (NCIT:C001, Q1) must have their
        # own graded-relevance tables. Q0 has a 4-entry table
        # (equiv-walk on D001); Q1 has a 1-entry table (ssbt-walk on
        # D000, which has no ancestors in this mini ontology beyond
        # the root, so only the gold gets gain).
        self.assertIn(("NCIT:C001", "Q0"), gains)
        self.assertIn(("NCIT:C001", "Q1"), gains)
        self.assertEqual(
            gains[("NCIT:C001", "Q1")],
            {("DOID:D000", "source_subsumed_by_target"): 1.0},
        )

    def test_end_to_end_scoring_counts_four_queries(self) -> None:
        """
        Full predict → score against the mini_paired fixture. The
        per-query count under every headline family must be 4. A
        pre-v0.1.3 implementation would report 2 (one per source).
        """
        import tempfile

        from biokg_align_kit.baselines import predict
        from biokg_align_kit.scoring import score_files

        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "predictions.tsv"
            predict(
                self.MINI_PAIRED, "NCIT-DOID", "valid",
                "hybrid_lexical", predictions,
            )
            metrics = score_files(
                predictions,
                self.MINI_PAIRED / "answers" / "NCIT-DOID.valid.answers.tsv",
            )
            self.assertEqual(metrics["queries"], 4.0)
            self.assertEqual(metrics["preferred_typed_queries"], 4.0)
            self.assertEqual(
                metrics["hierarchy_aware_typed_ndcg_at_10_queries"], 4.0
            )


class CanonicalFixtureTest(unittest.TestCase):
    """
    Coverage for the realistic-shape fixture (``examples/canonical/``).

    The canonical fixture has ``|C_q| = 50`` candidates per query —
    matching the official challenge shape — and exists so the kit's
    machinery can be exercised against realistic inputs without
    downloading the public dataset. Unlike the mini fixture (kept
    small for human readability), it is generated by a committed
    script for reproducibility.
    """

    def test_default_50_passes_without_warnings(self) -> None:
        """
        Run the hybrid_lexical baseline and validate at the default
        ``candidates_per_query=50``. Must produce no warnings — this
        is the central property the canonical fixture exists to
        demonstrate, in contrast to the mini fixture's 4-candidate
        warning.
        """
        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "predictions.tsv"
            predict(CANONICAL, "NCIT-DOID", "valid", "hybrid_lexical", predictions)

            result = validate_submission(
                predictions,
                CANONICAL / "tasks" / "NCIT-DOID" / "valid.cands.tsv",
            )
            self.assertFalse(result.errors)
            self.assertFalse(
                result.warnings,
                msg=f"Canonical fixture should validate cleanly; got: {result.warnings}",
            )

    def test_end_to_end_emits_all_three_metric_families(self) -> None:
        """
        Predict → score against the canonical fixture must emit
        diagnostic, preferred-pair, and hierarchy-aware nDCG keys —
        the canonical fixture ships ``*.preferred.tsv`` and
        ``*.graded.tsv`` alongside the answers.
        """
        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "predictions.tsv"
            predict(CANONICAL, "NCIT-DOID", "valid", "hybrid_lexical", predictions)

            metrics = score_files(
                predictions,
                CANONICAL / "answers" / "NCIT-DOID.valid.answers.tsv",
            )
            self.assertIn("diagnostic_relation_aware_ndcg_at_10", metrics)
            self.assertIn("preferred_typed_mrr", metrics)
            self.assertIn("hierarchy_aware_typed_ndcg_at_10", metrics)
            # Three queries in the canonical fixture.
            self.assertEqual(metrics["queries"], 3.0)
            self.assertEqual(metrics["preferred_typed_queries"], 3.0)
            self.assertEqual(metrics["hierarchy_aware_typed_ndcg_at_10_queries"], 3.0)

    def test_candidate_count_per_query_is_exactly_50(self) -> None:
        """
        Sanity check on the fixture itself: every query in
        ``valid.cands.tsv`` and ``test.cands.tsv`` has exactly 50
        candidates. If this fails, the fixture was committed out of
        sync with the generator.
        """
        from biokg_align_kit.io import parse_list, read_tsv

        for filename in ("valid.cands.tsv", "test.cands.tsv"):
            path = CANONICAL / "tasks" / "NCIT-DOID" / filename
            for row in read_tsv(path):
                candidates = parse_list(row["TgtCandidates"])
                self.assertEqual(
                    len(candidates), 50,
                    f"{filename} for source {row['SrcEntity']} should have "
                    f"50 candidates; found {len(candidates)}",
                )

    def test_generator_is_deterministic(self) -> None:
        """
        Re-running the generator script must produce byte-identical
        output. Protects against a future refactor introducing
        nondeterminism (set ordering, dict insertion order, etc.)
        that would invalidate the committed fixture.
        """
        import subprocess

        committed_files = [
            CANONICAL / "graph" / "properties.csv",
            CANONICAL / "graph" / "triples.csv",
            CANONICAL / "tasks" / "NCIT-DOID" / "valid.cands.tsv",
            CANONICAL / "tasks" / "NCIT-DOID" / "test.cands.tsv",
            CANONICAL / "answers" / "NCIT-DOID.valid.answers.tsv",
            CANONICAL / "answers" / "NCIT-DOID.valid.preferred.tsv",
        ]
        committed = {p.name: p.read_text() for p in committed_files}

        with tempfile.TemporaryDirectory() as tmp:
            # Copy the generator script into the temp dir so it
            # writes there. The script uses paths relative to its
            # own location.
            script_src = CANONICAL / "_build_canonical_fixture.py"
            tmp_path = Path(tmp)
            (tmp_path / "graph").mkdir()
            (tmp_path / "tasks").mkdir()
            (tmp_path / "answers").mkdir()
            script_copy = tmp_path / "_build.py"
            script_copy.write_text(script_src.read_text())

            result = subprocess.run(
                [sys.executable, str(script_copy)],
                capture_output=True,
                text=True,
                env={"PATH": "/usr/bin:/bin"},
            )
            self.assertEqual(
                result.returncode, 0,
                msg=f"Generator failed: stdout={result.stdout!r}, stderr={result.stderr!r}",
            )

            for committed_name, committed_text in committed.items():
                # Find the regenerated file by name anywhere under tmp.
                matches = list(tmp_path.rglob(committed_name))
                self.assertEqual(
                    len(matches), 1,
                    f"Expected exactly one regenerated {committed_name}; "
                    f"got {matches}",
                )
                self.assertEqual(
                    matches[0].read_text(), committed_text,
                    f"{committed_name} is not byte-identical to its "
                    f"committed version; regenerate the fixture.",
                )


if __name__ == "__main__":
    unittest.main()


# =============================================================================
# v0.1.3 Unit-4 — block-format submission validator
# =============================================================================

class BlockFormatValidatorTest(unittest.TestCase):
    """The block-scoring validator (validate_submission) enforces
    per-block positional layout matching cands.tsv row order."""

    _RELATIONS = ("equivalent", "source_subsumed_by_target", "source_subsumes_target")

    def _write_cands(self, path, rows):
        """Write a minimal test.cands.tsv (v0.1.3 schema: SrcEntity,
        TgtCandidates)."""
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("SrcEntity\tTgtCandidates\n")
            for (src, cands) in rows:
                cand_lit = str(cands).replace(" ", "")
                fh.write(f"{src}\t{cand_lit}\n")

    def _write_preds(self, path, rows):
        """Write a 4-column block-format submission."""
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("SrcEntity\tTgtEntity\tRelation\tScore\n")
            for (s, t, r, sc) in rows:
                fh.write(f"{s}\t{t}\t{r}\t{sc}\n")

    def _canonical_block(self, src, candidates):
        return [
            (src, tgt, rel, f"{1.0 / (1 + i):.4f}")
            for i, (tgt, rel) in enumerate(
                (tgt, rel) for tgt in candidates for rel in self._RELATIONS
            )
        ]

    def test_perfect_submission_passes(self) -> None:
        import tempfile
        from biokg_align_kit.validation import validate_submission

        with tempfile.TemporaryDirectory() as td:
            cands = Path(td) / "test.cands.tsv"
            preds = Path(td) / "preds.tsv"
            self._write_cands(cands, [
                ("s:1", ["t:0", "t:1"]),
                ("s:2", ["t:0", "t:1"]),
            ])
            self._write_preds(preds,
                self._canonical_block("s:1", ["t:0", "t:1"])
                + self._canonical_block("s:2", ["t:0", "t:1"]),
            )
            result = validate_submission(
                preds, cands, candidates_per_query=2,
            )
        self.assertEqual(result.errors, [])
        self.assertEqual(result.warnings, [])

    def test_row_count_mismatch_is_error(self) -> None:
        import tempfile
        from biokg_align_kit.validation import validate_submission

        with tempfile.TemporaryDirectory() as td:
            cands = Path(td) / "test.cands.tsv"
            preds = Path(td) / "preds.tsv"
            self._write_cands(cands, [("s:1", ["t:0", "t:1"])])
            # Submission has 5 rows instead of 6 (2 cands × 3 rels).
            self._write_preds(preds, [
                ("s:1", "t:0", "equivalent", "0.9"),
                ("s:1", "t:0", "source_subsumed_by_target", "0.8"),
                ("s:1", "t:0", "source_subsumes_target", "0.7"),
                ("s:1", "t:1", "equivalent", "0.6"),
                ("s:1", "t:1", "source_subsumed_by_target", "0.5"),
            ])
            result = validate_submission(
                preds, cands, candidates_per_query=2,
            )
        self.assertTrue(any("row count mismatch" in e.lower() for e in result.errors))

    def test_block_srcentity_mismatch_is_error(self) -> None:
        import tempfile
        from biokg_align_kit.validation import validate_submission

        with tempfile.TemporaryDirectory() as td:
            cands = Path(td) / "test.cands.tsv"
            preds = Path(td) / "preds.tsv"
            self._write_cands(cands, [("s:1", ["t:0", "t:1"])])
            block = self._canonical_block("s:1", ["t:0", "t:1"])
            block[3] = ("s:wrong", *block[3][1:])  # stray SrcEntity
            self._write_preds(preds, block)
            result = validate_submission(
                preds, cands, candidates_per_query=2,
            )
        self.assertTrue(any("s:wrong" in e for e in result.errors))

    def test_invalid_relation_is_error(self) -> None:
        import tempfile
        from biokg_align_kit.validation import validate_submission

        with tempfile.TemporaryDirectory() as td:
            cands = Path(td) / "test.cands.tsv"
            preds = Path(td) / "preds.tsv"
            self._write_cands(cands, [("s:1", ["t:0", "t:1"])])
            self._write_preds(preds, [
                ("s:1", "t:0", "equivalent", "0.9"),
                ("s:1", "t:0", "not_a_relation", "0.8"),
                ("s:1", "t:0", "source_subsumes_target", "0.7"),
                ("s:1", "t:1", "equivalent", "0.6"),
                ("s:1", "t:1", "source_subsumed_by_target", "0.5"),
                ("s:1", "t:1", "source_subsumes_target", "0.4"),
            ])
            result = validate_submission(
                preds, cands, candidates_per_query=2,
            )
        self.assertTrue(any("not_a_relation" in e for e in result.errors))

    def test_unparseable_score_is_error(self) -> None:
        import tempfile
        from biokg_align_kit.validation import validate_submission

        with tempfile.TemporaryDirectory() as td:
            cands = Path(td) / "test.cands.tsv"
            preds = Path(td) / "preds.tsv"
            self._write_cands(cands, [("s:1", ["t:0", "t:1"])])
            block = self._canonical_block("s:1", ["t:0", "t:1"])
            block[1] = (*block[1][:3], "not_a_number")
            self._write_preds(preds, block)
            result = validate_submission(
                preds, cands, candidates_per_query=2,
            )
        self.assertTrue(any("not_a_number" in e for e in result.errors))

    def test_duplicate_pair_is_warning(self) -> None:
        import tempfile
        from biokg_align_kit.validation import validate_submission

        with tempfile.TemporaryDirectory() as td:
            cands = Path(td) / "test.cands.tsv"
            preds = Path(td) / "preds.tsv"
            self._write_cands(cands, [("s:1", ["t:0", "t:1"])])
            self._write_preds(preds, [
                ("s:1", "t:0", "equivalent", "0.9"),
                ("s:1", "t:0", "source_subsumed_by_target", "0.8"),
                ("s:1", "t:0", "source_subsumes_target", "0.7"),
                ("s:1", "t:0", "equivalent", "0.1"),  # duplicate
                ("s:1", "t:1", "source_subsumed_by_target", "0.6"),
                ("s:1", "t:1", "source_subsumes_target", "0.5"),
            ])
            result = validate_submission(
                preds, cands, candidates_per_query=2,
            )
        # No fatal errors.
        self.assertEqual(result.errors, [])
        self.assertTrue(
            any("duplicate" in w.lower() for w in result.warnings),
            msg=f"Expected duplicate warning; got warnings={result.warnings}",
        )

    def test_non_candidate_tgt_is_warning_not_error(self) -> None:
        import tempfile
        from biokg_align_kit.validation import validate_submission

        with tempfile.TemporaryDirectory() as td:
            cands = Path(td) / "test.cands.tsv"
            preds = Path(td) / "preds.tsv"
            self._write_cands(cands, [("s:1", ["t:0", "t:1"])])
            self._write_preds(preds, [
                ("s:1", "t:0", "equivalent", "0.9"),
                ("s:1", "t:0", "source_subsumed_by_target", "0.8"),
                ("s:1", "t:0", "source_subsumes_target", "0.7"),
                ("s:1", "t:1", "equivalent", "0.6"),
                ("s:1", "t:1", "source_subsumed_by_target", "0.5"),
                ("s:1", "t:99", "source_subsumes_target", "0.4"),  # non-cand
            ])
            result = validate_submission(
                preds, cands, candidates_per_query=2,
            )
        # The non-candidate row is a warning (platform filters silently).
        self.assertEqual(result.errors, [])
        self.assertTrue(
            any("t:99" in w for w in result.warnings),
            msg=f"Expected warning about non-candidate t:99; got "
                f"warnings={result.warnings}",
        )

    def test_missing_pair_is_warning_against_disabled_candcount(self) -> None:
        import tempfile
        from biokg_align_kit.validation import validate_submission

        with tempfile.TemporaryDirectory() as td:
            cands = Path(td) / "test.cands.tsv"
            preds = Path(td) / "preds.tsv"
            self._write_cands(cands, [("s:1", ["t:0", "t:1"])])
            # Substitute (t:1, equivalent) with a non-canonical
            # (t:0, equivalent) duplicate. (t:1, equivalent) becomes
            # missing → warned.
            self._write_preds(preds, [
                ("s:1", "t:0", "equivalent", "0.9"),
                ("s:1", "t:0", "source_subsumed_by_target", "0.8"),
                ("s:1", "t:0", "source_subsumes_target", "0.7"),
                ("s:1", "t:0", "equivalent", "0.6"),  # dup, missing t:1/eq
                ("s:1", "t:1", "source_subsumed_by_target", "0.5"),
                ("s:1", "t:1", "source_subsumes_target", "0.4"),
            ])
            result = validate_submission(
                preds, cands, candidates_per_query=None,
            )
        self.assertEqual(result.errors, [])
        # Both duplicate AND missing-pair warnings fire.
        self.assertTrue(any("duplicate" in w.lower() for w in result.warnings))
        self.assertTrue(
            any("canonical (TgtEntity, Relation) pair" in w
                for w in result.warnings)
        )
