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
            predict(root / "examples" / "mini", "NCIT-DOID", "valid", "lexical", predictions)
            errors = validate_submission(
                predictions,
                root / "examples" / "mini" / "tasks" / "NCIT-DOID" / "valid.cands.tsv",
            )
            self.assertFalse(errors)
            metrics = score_files(
                predictions,
                root / "examples" / "mini" / "answers" / "NCIT-DOID.valid.answers.tsv",
            )
            self.assertIn("relation_aware_ndcg_at_10", metrics)


if __name__ == "__main__":
    unittest.main()
