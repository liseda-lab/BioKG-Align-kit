"""
Reference baselines for the BioKG-Align participant kit.

Two simple baselines ship with the kit:

* **``random``** — emits per-(source, target, relation) scores drawn
  from a seeded `random.Random` PRNG. Provides a calibration anchor
  for diagnostic metrics; under a 50-candidate x 3-relation block the
  expected MRR is ``H(150)/150 ≈ 0.038``.

* **``hybrid_lexical``** — emits a normalised lexical-overlap score
  between the source and target ``preferred_label`` + ``synonyms``
  strings, with a small additive bias toward the ``equivalent``
  relation. Mirrors the organiser-side ``hybrid_lexical`` baseline
  from the canonical Table 2 (paper §1.6); intended as a lower-bound
  reference rather than a competitive system.

The baseline catalogue is deliberately small. Participants are
expected to bring their own ranking model; these exist only to
exercise the kit end-to-end and to populate the canonical fixture.

Naming history (v0.2.0): the previous kit released this baseline
under the name ``lexical``. The canonical name organiser-side is
``hybrid_lexical`` (matches paper §1.6 Table 2), and the kit now
aligns. Calling ``score(..., baseline="lexical", ...)`` raises a
:class:`ValueError` with a one-line migration pointer.
"""

from __future__ import annotations

import random
from pathlib import Path

from .io import parse_list, read_tsv, write_tsv
from .text import lexical_score

# Canonical relation list emitted by every prediction row. Mirrors
# the organiser-side ``baselines.RELATIONS`` which is derived from
# ``config["submission"]["relations"]``. The kit hard-codes the
# canonical triple from paper §1.4.
RELATIONS: list[str] = [
    "equivalent",
    "source_subsumed_by_target",
    "source_subsumes_target",
]

# Baseline names accepted by :func:`predict` / :func:`score`. The
# legacy alias ``lexical`` is intentionally absent — callers using
# the old name receive a helpful error.
SUPPORTED_BASELINES: tuple[str, ...] = ("random", "hybrid_lexical")


def load_properties(data_dir: str | Path) -> dict[str, dict[str, str]]:
    path = Path(data_dir) / "graph" / "properties.csv"
    if not path.exists():
        return {}
    import csv

    with path.open("r", encoding="utf-8", newline="") as handle:
        return {row["node_id"]: row for row in csv.DictReader(handle)}


def predict(
    data_dir: str | Path,
    task: str,
    split: str,
    baseline: str,
    output: str | Path,
    seed: int = 17,
) -> Path:
    """
    Run a reference baseline end-to-end against a data directory.

    Reads ``tasks/<task>/<split>.cands.tsv`` from ``data_dir``, emits
    one prediction row per ``(SrcEntity, TgtEntity, Relation)`` tuple
    in block order, and writes the result as a 4-column TSV at
    ``output``. Each block of ``|C_q| x |RELATIONS|`` rows corresponds
    positionally to a row of the cands TSV — matching the v0.2.0
    block-scoring format participants submit to the platform.

    Parameters
    ----------
    data_dir
        Path containing ``tasks/`` and (for ``hybrid_lexical``) the
        ``graph/properties.csv`` file with per-node labels.
    task
        Task pair name (e.g. ``"NCIT-DOID"``).
    split
        ``"train"``, ``"valid"``, or ``"test"``.
    baseline
        One of :data:`SUPPORTED_BASELINES`.
    output
        Destination TSV path.
    seed
        RNG seed for ``random``. ``hybrid_lexical`` is deterministic
        and ignores the seed.
    """
    data_path = Path(data_dir)
    candidates = read_tsv(data_path / "tasks" / task / f"{split}.cands.tsv")
    properties = load_properties(data_path)
    rows = []
    for row in candidates:
        source_id = row["SrcEntity"]
        for target_id in parse_list(row["TgtCandidates"]):
            for relation in RELATIONS:
                rows.append({
                    "SrcEntity": source_id,
                    "TgtEntity": target_id,
                    "Relation": relation,
                    "Score": f"{score(source_id, target_id, relation, properties, baseline, seed):.8f}",
                })
    write_tsv(output, rows, ["SrcEntity", "TgtEntity", "Relation", "Score"])
    return Path(output)


def score(
    source_id: str,
    target_id: str,
    relation: str,
    properties: dict[str, dict[str, str]],
    baseline: str,
    seed: int,
) -> float:
    """
    Compute the baseline score for a single ``(source, target,
    relation)`` triple.

    Raises
    ------
    ValueError
        If ``baseline`` is not in :data:`SUPPORTED_BASELINES`. The
        legacy alias ``"lexical"`` (used in pre-v0.2.0 kits) raises a
        ValueError with a one-line migration pointer.
    """
    if baseline == "random":
        return random.Random(
            f"{seed}:{source_id}:{target_id}:{relation}"
        ).random()
    if baseline == "hybrid_lexical":
        source = properties.get(source_id, {})
        target = properties.get(target_id, {})
        source_text = " ".join([
            source.get("preferred_label", ""),
            source.get("synonyms", ""),
        ])
        target_text = " ".join([
            target.get("preferred_label", ""),
            target.get("synonyms", ""),
        ])
        relation_bias = 0.05 if relation == "equivalent" else 0.0
        return lexical_score(source_text, target_text) + relation_bias
    if baseline == "lexical":
        raise ValueError(
            "Baseline 'lexical' was renamed to 'hybrid_lexical' in "
            "v0.2.0 to match the canonical organiser baseline name "
            "(paper §1.6 Table 2). Pass --baseline hybrid_lexical."
        )
    raise ValueError(
        f"Unsupported baseline: {baseline!r}. Choose one of "
        f"{list(SUPPORTED_BASELINES)}."
    )
