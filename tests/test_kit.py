from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from biokg_align_kit.baselines import predict
from biokg_align_kit.scoring import score_files
from biokg_align_kit.validation import validate_submission


class KitTest(unittest.TestCase):
    def test_baseline_scores_and_validates(self) -> None:
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            predictions = Path(tmp) / "predictions.tsv"
            predict(root / "examples" / "mini", "NCIT-DOID", "valid", "hybrid_lexical", predictions)
            errors = validate_submission(
                predictions,
                root / "examples" / "mini" / "tasks" / "NCIT-DOID" / "valid.cands.tsv",
                # the mini fixture uses 4 candidates per query, not the
                # official 50 — None skips the candidate-count check
                candidates_per_query=None,
            )
            self.assertFalse(errors)
            metrics = score_files(
                predictions,
                root / "examples" / "mini" / "answers" / "NCIT-DOID.valid.answers.tsv",
            )
            self.assertIn("diagnostic_relation_aware_ndcg_at_10", metrics)

    def test_removed_lexical_baseline_raises_migration_error(self) -> None:
        # 'lexical' was renamed to 'hybrid_lexical' in v0.2.0; the legacy name
        # must fail loudly with the migration message, never run silently.
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as ctx:
                predict(
                    root / "examples" / "mini",
                    "NCIT-DOID",
                    "valid",
                    "lexical",
                    Path(tmp) / "predictions.tsv",
                )
            self.assertIn("hybrid_lexical", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
