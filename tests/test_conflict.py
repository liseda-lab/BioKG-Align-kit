from __future__ import annotations

import unittest
import tempfile
from pathlib import Path

from biokg_align_kit.conflict import mapping_to_rdf, select_top_mappings
from biokg_align_kit.datalog import load_rules


class PublicDatalogConflictTest(unittest.TestCase):
    def test_public_translation_and_selection_match_contract(self) -> None:
        self.assertEqual(
            ("T", "http://www.w3.org/2000/01/rdf-schema#subClassOf", "S"),
            mapping_to_rdf(("S", "T", "source_subsumes_target")),
        )
        rows = [
            {"SrcEntity": "S", "QueryID": "Q0", "TgtEntity": "B", "Relation": "equivalent", "Score": "1"},
            {"SrcEntity": "S", "QueryID": "Q0", "TgtEntity": "A", "Relation": "equivalent", "Score": "1"},
        ]
        self.assertEqual(
            [("S", "A", "equivalent")],
            select_top_mappings(rows),
        )

    def test_load_rules_resolves_split_core_driver(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "facts.dl").write_text('seed("x").\n', encoding="utf-8")
            (root / "owl2rl_core.dl").write_text(
                "derived(x) :- seed(x).\n",
                encoding="utf-8",
            )
            (root / "rules.dl").write_text(
                '.include "owl2rl_core.dl"\n.include "facts.dl"\n.output derived\n',
                encoding="utf-8",
            )
            rules = load_rules(root / "rules.dl")
            self.assertEqual(1, len(rules))
            self.assertEqual("derived", rules[0].head.predicate)


if __name__ == "__main__":
    unittest.main()
