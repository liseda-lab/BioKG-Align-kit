"""
Block-format submission validator for BioKG-Align.

Per paper §2.1, a well-formed submission must contain exactly
|C_q| * |R_A| = 50 * 3 = 150 rows per query. Missing pairs are filled
with score 0.0 (effective last-rank) by the platform-side evaluation
pipeline; this validator treats missing pairs as a *warning* so that
participants are informed early without having their local validation fail.

The expected number of candidates per query is configurable via
candidates_per_query. It defaults to 50 (the official challenge value).
Pass None to skip the candidate-count check on fixtures with non-canonical
candidate counts (e.g., the bundled examples/mini fixture).

The participant-facing CLI (biokg-align-kit verify) invokes
:func:`validate_submission`, which validates the canonical block-scoring
contract per paper §2.1. The validator returns a :class:`ValidationResult`
with errors and warnings lists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .io import parse_list, read_tsv

RELATIONS: tuple[str, ...] = (
    "equivalent",
    "source_subsumed_by_target",
    "source_subsumes_target",
)
REQUIRED_COLUMNS = {"SrcEntity", "TgtEntity", "Relation", "Score"}

# Default number of candidates per query in the official challenge.
DEFAULT_CANDIDATES_PER_QUERY = 50


@dataclass
class ValidationResult:
    """
    Outcome of a submission validation pass.

    errors are conditions that would cause the platform to reject the
    submission outright (missing columns, unknown source entities, etc.).
    warnings are conditions the platform tolerates but participants
    should know about (e.g., missing candidate-relation pairs, which the
    platform fills with score -inf).
    """

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        # Preserves the legacy if errors: ... idiom: result is truthy
        # exactly when there are blocking errors. Warnings alone don't
        # make the result truthy.
        return bool(self.errors)

    def __iter__(self):
        # Preserves the legacy for error in errors: ... idiom.
        return iter(self.errors)

    def __len__(self) -> int:
        # Preserves the legacy len(errors) idiom.
        return len(self.errors)


def validate_submission(
    predictions_path: str | Path,
    candidates_path: str | Path,
    *,
    candidates_per_query: int | None = DEFAULT_CANDIDATES_PER_QUERY,
    relations: tuple[str, ...] | list[str] = RELATIONS,
) -> ValidationResult:
    """
    Validate a prediction TSV against a candidate file under the
    block-scoring format (paper §2.1).

    The submission is treated as a sequence of N_queries blocks, each of
    size candidates_per_query * len(relations) (canonical 50 * 3 =
    150). Block k corresponds positionally to row k of
    candidates_path. Within a block, the (TgtEntity, Relation)
    layout must cover the cartesian product candidates[k] x relations.

    Errors (submission would be rejected by the platform)
    -----------------------------------------------------

      * Missing column among {SrcEntity, TgtEntity, Relation, Score}.
      * Total row count != N_queries * block_size (rejected outright).
      * A block contains a row whose SrcEntity disagrees with the
        canonical cands.tsv row for that block.
      * A row's Relation is not in relations.
      * A row's Score is not parseable as a float.

    Warnings (platform tolerates; participant should know)
    -----------------------------------------------------

      * A block contains a row whose TgtEntity is not in that
        block's canonical candidate set (the platform silently filters
        it; the missing canonical pair it should have occupied counts
        as a missing pair below).
      * Duplicate (TgtEntity, Relation) pair within a block (the
        platform takes the maximum score).
      * Missing canonical (TgtEntity, Relation) pair within a block
        (the platform assigns score 0.0 = effective last rank).
    """
    result = ValidationResult()
    relations_tuple = tuple(relations)
    relations_set = set(relations_tuple)

    predictions = read_tsv(predictions_path)
    if predictions:
        missing = REQUIRED_COLUMNS - set(predictions[0])
        if missing:
            result.errors.append(
                f"Prediction file is missing columns: {sorted(missing)}"
            )
            # No point doing per-row work without the required columns.
            return result

    candidates_rows = list(read_tsv(candidates_path))
    n_queries = len(candidates_rows)
    if candidates_per_query is None:
        # Caller asked to skip the candidate-count check (e.g., on the
        # bundled mini fixture). Derive the per-block expected
        # candidate count from each row's TgtCandidates list rather
        # than from a fixed integer.
        per_block_cand_counts = [
            len(parse_list(row.get("TgtCandidates", "[]")))
            for row in candidates_rows
        ]
    else:
        per_block_cand_counts = [candidates_per_query] * n_queries
        for row_idx, row in enumerate(candidates_rows):
            actual = len(parse_list(row.get("TgtCandidates", "[]")))
            if actual != candidates_per_query:
                result.warnings.append(
                    f"Candidates file row {row_idx} (SrcEntity "
                    f"{row.get('SrcEntity', '<missing>')!r}): "
                    f"{actual} candidates; the official challenge uses "
                    f"{candidates_per_query}. Local validation will not "
                    f"match leaderboard behaviour."
                )

    # Block boundary mapping: cumulative row index per block.
    block_starts: list[int] = []
    acc = 0
    for cc in per_block_cand_counts:
        block_starts.append(acc)
        acc += cc * len(relations_tuple)
    expected_total = acc

    if len(predictions) != expected_total:
        result.errors.append(
            f"Block-format submission row count mismatch: got "
            f"{len(predictions)} rows, expected {expected_total} "
            f"({n_queries} queries x block sizes "
            f"{[cc * len(relations_tuple) for cc in per_block_cand_counts][:5]}"
            f"{'... ' if n_queries > 5 else ''}"
            f"). The submission must contain exactly the canonical "
            f"block-size rows per query in the same row order as "
            f"cands.tsv."
        )
        return result

    # Per-block validation:
    for block_idx, cand_row in enumerate(candidates_rows):
        block_src = cand_row.get("SrcEntity", "")
        block_candidates = parse_list(cand_row.get("TgtCandidates", "[]"))
        block_candidates_set = set(block_candidates)
        block_size = per_block_cand_counts[block_idx] * len(relations_tuple)
        start = block_starts[block_idx]
        end = start + block_size

        seen_pairs: set[tuple[str, str]] = set()
        duplicate_pairs: set[tuple[str, str]] = set()
        for row_offset, pred_row in enumerate(predictions[start:end]):
            row_line = start + row_offset + 2  # +2: header + 1-indexed
            sub_src = pred_row.get("SrcEntity", "")
            sub_tgt = pred_row.get("TgtEntity", "")
            sub_rel = pred_row.get("Relation", "")
            sub_score = pred_row.get("Score", "")

            if sub_src != block_src:
                result.errors.append(
                    f"Line {row_line}: SrcEntity {sub_src!r} does not "
                    f"match the canonical SrcEntity {block_src!r} for "
                    f"block {block_idx} (per cands.tsv row order)."
                )
                # Don't accumulate the row into seen_pairs — its block
                # membership is invalid.
                continue

            if sub_rel not in relations_set:
                result.errors.append(
                    f"Line {row_line}: Relation {sub_rel!r} is not in "
                    f"the canonical relation set {sorted(relations_set)}."
                )

            try:
                float(sub_score)
            except (TypeError, ValueError):
                result.errors.append(
                    f"Line {row_line}: Score {sub_score!r} is not "
                    f"parseable as a float."
                )

            if sub_tgt not in block_candidates_set:
                result.warnings.append(
                    f"Line {row_line}: TgtEntity {sub_tgt!r} is not in "
                    f"the canonical candidate set for block {block_idx} "
                    f"(SrcEntity {block_src!r}); the platform will "
                    f"silently filter this row."
                )
                continue

            pair = (sub_tgt, sub_rel)
            if pair in seen_pairs:
                duplicate_pairs.add(pair)
            else:
                seen_pairs.add(pair)

        if duplicate_pairs:
            result.warnings.append(
                f"Block {block_idx} (SrcEntity {block_src!r}): "
                f"{len(duplicate_pairs)} duplicate (TgtEntity, Relation) "
                f"pair(s); the platform will keep the maximum score per "
                f"pair. First few: {sorted(duplicate_pairs)[:5]}"
                + (" ..." if len(duplicate_pairs) > 5 else "")
            )

        canonical_pairs = {
            (tgt, rel) for tgt in block_candidates for rel in relations_tuple
        }
        missing = canonical_pairs - seen_pairs
        if missing:
            result.warnings.append(
                f"Block {block_idx} (SrcEntity {block_src!r}): "
                f"submission covers "
                f"{len(canonical_pairs - missing)}/{len(canonical_pairs)} "
                f"canonical (TgtEntity, Relation) pairs. The platform "
                f"will assign score 0.0 to the missing "
                f"{len(missing)} pair(s) (effective last-rank)."
            )

    return result
