"""
Scoring helpers for the BioKG-Align kit.

This module ships three metric families. The intent and naming match
the organiser-side biokg_align.scoring module so that local kit
scores under the headline keys are directly comparable to the
leaderboard:

1. **Preferred-pair metrics** (paper §1.5 primary).
   For each query, exactly one preferred (target, relation) gold
   pair is fixed organiser-side. Metrics: preferred_typed_mrr,
   preferred_typed_hits_at_{1,5,10}. The diagnostic
   preferred_entity_relation_accuracy and
   preferred_entity_relation_macro_f1 are also emitted from the
   preferred-pair family — they isolate the relation-typing
   sub-problem conditional on entity correctness.

2. **Hierarchy-Aware Typed nDCG@10** (paper §1.5 secondary).
   Continuous graded gains over hierarchically close
   (target, relation) pairs. Output key:
   hierarchy_aware_typed_ndcg_at_10.

3. **Diagnostic set-based metrics** (kit-only).
   Each query contributes its full gold set; the relevance signal is
   binary set membership against any gold pair. Output keys are
   prefixed diagnostic_*. Useful for fast iteration but **not** the
   leaderboard score — participants should not treat these as the
   headline.

Per-query keying
----------------
All loaders and scorers in this module key by the (SrcEntity,
QueryID) tuple. Under the pool model the same SrcEntity
contributes Q0 (equivalence) and Q1 (subsumption) queries with
distinct gold pairs and candidate pools; collapsing by SrcEntity
alone would silently merge them.
Files without a QueryID column fall back to "Q0" for every row,
preserving compatibility with legacy fixtures.

Predictions arrive in the 4-column block format (no QueryID
column); :func:`load_block_format_predictions` recovers the per-query
partitioning positionally from the answers file's row order, matching
the platform-side contract.
"""

from __future__ import annotations

import math
import warnings
from collections import defaultdict
from pathlib import Path

from .io import parse_list, read_tsv, write_json


# Explicit relation ordering for ranking tie-breaks per paper §2.1.
#
# When two predictions share a score AND a target entity, the relation
# that appears earliest in this map is ranked first. The order is:
#   equivalent ≺ source_subsumed_by_target ≺ source_subsumes_target.
RELATION_TIEBREAK_ORDER: dict[str, int] = {
    "equivalent": 0,
    "source_subsumed_by_target": 1,
    "source_subsumes_target": 2,
}

# Default canonical relation set; mirrors paper §1.4. The organiser
# configures this via config["submission"]["relations"]; the kit
# hard-codes it because the public build always uses the canonical
# triple. The validator accepts an override for hypothetical builds.
DEFAULT_RELATIONS: tuple[str, ...] = (
    "equivalent",
    "source_subsumed_by_target",
    "source_subsumes_target",
)

# Unknown relations sort after all known ones; they will already have
# been flagged by the submission validator before reaching the scorer.
_UNKNOWN_RELATION_RANK = len(RELATION_TIEBREAK_ORDER)


def _rank_key(row: dict[str, str]) -> tuple[float, str, int]:
    """
    Sort key for ranking prediction rows within a single query.
    Descending by score, ascending by TgtEntity, ascending by the
    explicit relation order above.
    """
    return (
        -float(row.get("Score", 0.0)),
        row["TgtEntity"],
        RELATION_TIEBREAK_ORDER.get(row["Relation"], _UNKNOWN_RELATION_RANK),
    )


# =========================================================================
# Loaders
# =========================================================================


def load_answers(
    path: str | Path,
) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """
    Load per-query gold answers from an answers/cands TSV.

    Supports both shapes:

    * **Train/valid public cands** (7 columns):
      SrcEntity, QueryID, GoldTarget, Relation, TgtEntities,
      Relations, TgtCandidates. Gold is read from TgtEntities
      and Relations (list-valued); GoldTarget and Relation
      are the singleton form and are ignored here.
    * **Private test answers** (same shape; QueryID retained).
    * **Legacy v0.1.2 cands** (4 columns, no QueryID): SrcEntity,
      TgtEntities, Relations, TgtCandidates. The missing QueryID
      defaults to "Q0".

    Returns
    -------
    dict[(SrcEntity, QueryID), set[(TgtEntity, Relation)]]
        Per-query gold pair set.
    """
    answers: dict[tuple[str, str], set[tuple[str, str]]] = defaultdict(set)
    for row in read_tsv(path):
        src = row["SrcEntity"]
        query_id = row.get("QueryID", "Q0")
        if row.get("TgtEntities", "").startswith("["):
            targets = parse_list(row["TgtEntities"])
        else:
            targets = [row["TgtEntity"]]
        if row.get("Relations", "").startswith("["):
            relations = parse_list(row["Relations"])
        else:
            relations = [row["Relation"]]
        if len(targets) != len(relations):
            raise ValueError(
                f"Answer target/relation list length mismatch for "
                f"({src!r}, {query_id!r})"
            )
        for target, relation in zip(targets, relations):
            answers[(src, query_id)].add((target, relation))
    return answers


def load_per_query_candidate_sets(
    path: str | Path,
) -> dict[tuple[str, str], set[str]]:
    """
    Load per-query candidate sets from a cands or answers TSV.

    Returns {(SrcEntity, QueryID): {candidate_id, ...}} from the
    TgtCandidates column. Files without a QueryID column fall
    back to "Q0".

    Note: the public test.cands.tsv (v0.2.0 schema) has only
    SrcEntity, TgtCandidates. Under the pool model, the same
    SrcEntity legitimately appears twice (Q0 + Q1); calling this
    helper on test.cands.tsv would silently merge them under the
    "Q0" fallback. For positional per-query work against a public
    test file, use the block-format prediction loader (which walks the
    cands file by row order, not by source key) instead.
    """
    sets: dict[tuple[str, str], set[str]] = {}
    for row in read_tsv(path):
        src = row["SrcEntity"]
        query_id = row.get("QueryID", "Q0")
        candidates = parse_list(row.get("TgtCandidates", "[]"))
        sets[(src, query_id)] = set(candidates)
    return sets


def load_preferred_pairs(
    path: str | Path,
) -> dict[tuple[str, str], tuple[str, str]]:
    """
    Load per-query preferred (target, relation) gold pairs.

    File schema (v0.2.0): SrcEntity, QueryID, TgtEntity, Relation.
    Files without a QueryID column fall back to "Q0". Under
    the pool model, the per-(SrcEntity, QueryID) keying preserves
    both the Q0 and Q1 preferred pairs; collapsing by SrcEntity
    alone would silently overwrite one with the other.

    Returns
    -------
    dict[(SrcEntity, QueryID), (TgtEntity, Relation)]
        Per-query preferred pair. Empty dict if path does not
        exist. Each query has exactly one preferred pair under the
        canonical v0.2.0 build; duplicate rows for the same key are
        silently deduplicated, last write wins (organiser-side emit
        guarantees uniqueness, so this branch only fires on
        hand-rolled inputs).
    """
    path = Path(path)
    if not path.exists():
        return {}
    preferred: dict[tuple[str, str], tuple[str, str]] = {}
    for row in read_tsv(path):
        src = row["SrcEntity"]
        query_id = row.get("QueryID", "Q0")
        preferred[(src, query_id)] = (row["TgtEntity"], row["Relation"])
    return preferred


def load_block_format_predictions(
    predictions_path: str | Path,
    answers_path: str | Path,
    relations: tuple[str, ...] | list[str] = DEFAULT_RELATIONS,
    candidate_count: int = 50,
    strict: bool = True,
) -> list[dict[str, str]]:
    """
    Load a participant submission in the v0.2.0 block-scoring format.

    Block-scoring format
    --------------------
    
    The submission TSV has four columns:

      SrcEntity, TgtEntity, Relation, Score

    There is no QueryID column. Rows are grouped positionally into
    blocks of size candidate_count x len(relations) (canonical:
    50 x 3 = 150). Block k corresponds positionally to the k-th
    row of the answers TSV (which carries QueryID and TgtCandidates).

    Within a block, rows can appear in any order; the loader indexes
    by (TgtEntity, Relation) and emits one output row per canonical 
    (candidate, relation) pair drawn from the answers TSV's TgtCandidates 
    list, with duplicates max-merged and missing pairs zero-filled.

    Strictness rules
    ----------------

    +-----------------------------------------------------+-----------------+
    | Condition                                           | Behaviour       |
    +=====================================================+=================+
    | Total row count != N_queries x block_size           | Fatal           |
    +-----------------------------------------------------+-----------------+
    | A block contains rows whose SrcEntity disagrees     | Fatal           |
    | with the corresponding answers row                  |                 |
    +-----------------------------------------------------+-----------------+
    | Row's Relation not in relations                 | Fatal           |
    +-----------------------------------------------------+-----------------+
    | Score column not parseable as float                 | Fatal           |
    +-----------------------------------------------------+-----------------+
    | Duplicate (TgtEntity, Relation) pair within a block | Warn, take max  |
    +-----------------------------------------------------+-----------------+
    | Missing (TgtEntity, Relation) pair within a block   | Warn, score 0   |
    +-----------------------------------------------------+-----------------+
    | Row's TgtEntity not in that query's candidate set   | Silently filter |
    +-----------------------------------------------------+-----------------+

    Fatal conditions raise a ValueError; warn conditions emit a UserWarning. 
    The silent filter drops the offending row from the output and the canonical 
    pair it should have occupied is surfaced by the missing-pair warning below.

    Parameters
    ----------
    predictions_path
        Participant submission TSV in block-scoring format.
    answers_path
        Either the public train/valid cands TSV (for self-scoring) or
        the private test answers TSV. Both carry QueryID and TgtCandidates.
    relations
        Canonical relation list. Defaults to DEFAULT_RELATIONS.
    candidate_count
        Number of candidates per query. Canonical 50.
    strict
        When True (default), all rules above apply. When False, the
        loader skips per-row validation and emits raw rows with
        QueryID propagated — useful for round-tripping legacy
        fixtures.

    Returns
    -------
    list[dict[str, str]]
        Rows with columns SrcEntity, QueryID, TgtEntity, Relation, Score.
    """
    relations_tuple = tuple(relations)
    relations_set = set(relations_tuple)
    block_size = int(candidate_count) * len(relations_tuple)
    if block_size <= 0:
        raise ValueError(
            f"load_block_format_predictions: block_size = {block_size}; "
            f"candidate_count={candidate_count}, "
            f"relations={list(relations_tuple)}"
        )

    answer_rows = list(read_tsv(answers_path))
    n_queries = len(answer_rows)
    submission_rows = list(read_tsv(predictions_path))
    expected_total = n_queries * block_size
    if len(submission_rows) != expected_total:
        raise ValueError(
            f"Block-format submission row count mismatch: got "
            f"{len(submission_rows)} rows, expected {expected_total} "
            f"({n_queries} queries x {block_size} block size). The "
            f"submission must contain exactly {block_size} rows per "
            f"query in the canonical cands.tsv / answers.tsv row order."
        )

    if not strict:
        enriched_loose: list[dict[str, str]] = []
        for block_idx, answer_row in enumerate(answer_rows):
            block_src = answer_row["SrcEntity"]
            block_query_id = answer_row.get("QueryID", "Q0")
            start = block_idx * block_size
            end = start + block_size
            for sub_row in submission_rows[start:end]:
                enriched_loose.append({
                    "SrcEntity": sub_row.get("SrcEntity", block_src),
                    "QueryID": block_query_id,
                    "TgtEntity": sub_row.get("TgtEntity", ""),
                    "Relation": sub_row.get("Relation", ""),
                    "Score": sub_row.get("Score", "0"),
                })
        return enriched_loose

    enriched: list[dict[str, str]] = []
    for block_idx, answer_row in enumerate(answer_rows):
        block_src = answer_row["SrcEntity"]
        block_query_id = answer_row.get("QueryID", "Q0")
        block_candidates = set(parse_list(answer_row.get("TgtCandidates", "[]")))
        start = block_idx * block_size
        end = start + block_size

        block_index: dict[tuple[str, str], float] = {}
        duplicates_seen: set[tuple[str, str]] = set()
        for row_offset, sub_row in enumerate(submission_rows[start:end]):
            row_idx = start + row_offset
            sub_src = sub_row.get("SrcEntity", "")
            if sub_src != block_src:
                raise ValueError(
                    f"Block-format submission row {row_idx}: SrcEntity "
                    f"{sub_src!r} does not match the canonical SrcEntity "
                    f"{block_src!r} for block {block_idx} (per the "
                    f"answers/cands row order). Each block of "
                    f"{block_size} consecutive rows must share a single "
                    f"SrcEntity, in the order documented in "
                    f"documentation/submission_format.md."
                )
            tgt = sub_row.get("TgtEntity", "")
            rel = sub_row.get("Relation", "")
            if rel not in relations_set:
                raise ValueError(
                    f"Block-format submission row {row_idx}: Relation "
                    f"{rel!r} is not in the canonical relation list "
                    f"{sorted(relations_set)}."
                )
            score_str = sub_row.get("Score", "")
            try:
                score = float(score_str)
            except (TypeError, ValueError):
                raise ValueError(
                    f"Block-format submission row {row_idx}: Score "
                    f"{score_str!r} is not parseable as a float."
                ) from None

            if tgt not in block_candidates:
                # Silent: drop rows whose TgtEntity isn't in the
                # query's candidate set. The missing canonical pair
                # the row should have occupied surfaces in the Pass-2
                # missing-pair warning below.
                continue

            key = (tgt, rel)
            if key in block_index:
                duplicates_seen.add(key)
                if score > block_index[key]:
                    block_index[key] = score
            else:
                block_index[key] = score

        if duplicates_seen:
            warnings.warn(
                f"Block {block_idx} (SrcEntity {block_src!r}, "
                f"QueryID {block_query_id!r}): "
                f"{len(duplicates_seen)} duplicate (TgtEntity, Relation) "
                f"pair(s); kept the maximum score per pair. First few: "
                f"{sorted(duplicates_seen)[:5]}"
                + (" ..." if len(duplicates_seen) > 5 else ""),
                UserWarning,
                stacklevel=2,
            )

        missing_pairs: list[tuple[str, str]] = []
        for tgt in sorted(block_candidates):
            for rel in relations_tuple:
                key = (tgt, rel)
                if key in block_index:
                    score_val = block_index[key]
                else:
                    score_val = 0.0
                    missing_pairs.append(key)
                enriched.append({
                    "SrcEntity": block_src,
                    "QueryID": block_query_id,
                    "TgtEntity": tgt,
                    "Relation": rel,
                    "Score": f"{score_val:.6f}",
                })

        if missing_pairs:
            warnings.warn(
                f"Block {block_idx} (SrcEntity {block_src!r}, "
                f"QueryID {block_query_id!r}): "
                f"{len(missing_pairs)} canonical (TgtEntity, Relation) "
                f"pair(s) missing from the submission; assigned score "
                f"0.0 (effective last-rank). First few: "
                f"{missing_pairs[:5]}"
                + (" ..." if len(missing_pairs) > 5 else ""),
                UserWarning,
                stacklevel=2,
            )

    return enriched



def ndcg(relevance: list[int], k: int, ideal_count: int) -> float:
    dcg = sum(rel / math.log2(index + 2) for index, rel in enumerate(relevance[:k]))
    idcg = sum(1.0 / math.log2(index + 2) for index in range(min(k, ideal_count)))
    return dcg / idcg if idcg else 0.0


def reciprocal_rank(relevance: list[int]) -> float:
    for index, rel in enumerate(relevance, start=1):
        if rel:
            return 1.0 / index
    return 0.0


def average_precision(relevance: list[int], gold_count: int) -> float:
    if gold_count == 0:
        return 0.0
    total = 0.0
    found = 0
    for index, rel in enumerate(relevance, start=1):
        if rel:
            found += 1
            total += found / index
    return total / gold_count


def macro_f1(tp: dict[str, int], fp: dict[str, int], fn: dict[str, int]) -> float:
    relations = sorted(set(tp) | set(fp) | set(fn))
    if not relations:
        return 0.0
    scores = []
    for relation in relations:
        precision = tp[relation] / (tp[relation] + fp[relation]) if tp[relation] + fp[relation] else 0.0
        recall = tp[relation] / (tp[relation] + fn[relation]) if tp[relation] + fn[relation] else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return mean(scores)


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


####
# Per-query scorer (the entry point most callers want)
###


def score_prediction_rows(
    predictions: list[dict[str, str]],
    answers: dict[tuple[str, str], set[tuple[str, str]]],
    k: int = 10,
    preferred_pairs: dict[tuple[str, str], tuple[str, str]] | None = None,
    graded_relevance: dict[tuple[str, str], dict[tuple[str, str], float]] | None = None,
) -> dict[str, float]:
    """
    Compute metrics for prediction rows against per-query gold.

    All inputs are keyed by (SrcEntity, QueryID). Predictions
    arrive enriched with a QueryID column (typically from
    :func:`load_block_format_predictions`); rows are partitioned by
    that key for per-query scoring.

    Output keys:

    * diagnostic_* — kit-only set-based metrics over the full gold
      set. Not the leaderboard score.
    * preferred_typed_* — preferred-pair MRR + Hits@K (paper §1.5
      primary). Emitted when preferred_pairs is supplied.
    * preferred_entity_relation_* — Macro-F1 on entity-correct
      subset (paper §1.5 (ii)/(iii)). Emitted with preferred_pairs.
    * hierarchy_aware_typed_ndcg_at_10 — H-nDCG@10 (paper §1.5
      secondary). Emitted when graded_relevance is supplied.
    """
    # Partition predictions by (SrcEntity, QueryID):
    by_query: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in predictions:
        key = (row["SrcEntity"], row.get("QueryID", "Q0"))
        by_query[key].append(row)

    # Diagnostic accumulators:
    ndcgs: list[float] = []
    mrrs: list[float] = []
    hits1: list[float] = []
    hits5: list[float] = []
    hits10: list[float] = []
    aps: list[float] = []
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)

    # Preferred-pair accumulators:
    pref_rrs: list[float] = []
    pref_hits1: list[float] = []
    pref_hits5: list[float] = []
    pref_hits10: list[float] = []
    pref_tp: dict[str, int] = defaultdict(int)
    pref_fp: dict[str, int] = defaultdict(int)
    pref_fn: dict[str, int] = defaultdict(int)
    pref_entity_correct_count = 0
    pref_top_relation_correct_count = 0

    # Hierarchy-aware accumulators:
    h_ndcgs: list[float] = []

    # Local imports to avoid module-level circular dependency:
    from .hierarchy import hierarchy_aware_ndcg

    for query_key, gold in sorted(answers.items()):
        ranked = sorted(by_query.get(query_key, []), key=_rank_key)

        # diagnostic (set-based) metrics
        relevance = [
            1 if (row["TgtEntity"], row["Relation"]) in gold else 0
            for row in ranked
        ]
        ndcgs.append(ndcg(relevance, min(k, len(ranked)), ideal_count=len(gold)))
        mrrs.append(reciprocal_rank(relevance))
        hits1.append(1.0 if any(relevance[:1]) else 0.0)
        hits5.append(1.0 if any(relevance[:5]) else 0.0)
        hits10.append(1.0 if any(relevance[:10]) else 0.0)
        aps.append(average_precision(relevance, len(gold)))

        predicted_positive = {(row["TgtEntity"], row["Relation"]) for row in ranked[:1]}
        for pair in predicted_positive:
            relation = pair[1]
            if pair in gold:
                tp[relation] += 1
            else:
                fp[relation] += 1
        for pair in gold - predicted_positive:
            fn[pair[1]] += 1

        # Preferred-pair metrics:
        if preferred_pairs is not None and query_key in preferred_pairs:
            preferred = preferred_pairs[query_key]
            pref_relevance = [
                1 if (row["TgtEntity"], row["Relation"]) == preferred else 0
                for row in ranked
            ]
            pref_rrs.append(reciprocal_rank(pref_relevance))
            pref_hits1.append(1.0 if any(pref_relevance[:1]) else 0.0)
            pref_hits5.append(1.0 if any(pref_relevance[:5]) else 0.0)
            pref_hits10.append(1.0 if any(pref_relevance[:10]) else 0.0)

            # Relation Macro-F1 on the Preferred Entity (paper §1.5).
            # Collapse the per-query ranking to entity-only by taking
            # the max score per entity; if the top entity matches the
            # preferred target, contribute one observation to the F1
            # accumulators using the top-1 row's relation as the
            # system's relation prediction.

            best_score_by_entity: dict[str, float] = {}
            for position, row in enumerate(ranked):
                try:
                    score = float(row.get("Score", -position))
                except (TypeError, ValueError):
                    score = -float(position)
                tgt = row["TgtEntity"]
                if tgt not in best_score_by_entity or score > best_score_by_entity[tgt]:
                    best_score_by_entity[tgt] = score

            if best_score_by_entity:
                top_entity = max(
                    best_score_by_entity.keys(),
                    key=lambda e: (best_score_by_entity[e],
                                   -ranked.index(next(r for r in ranked
                                                       if r["TgtEntity"] == e))),
                )
                preferred_target, preferred_relation = preferred
                if top_entity == preferred_target:
                    pref_entity_correct_count += 1
                    top_row = ranked[0]
                    predicted_relation = top_row["Relation"]
                    if predicted_relation == preferred_relation:
                        pref_tp[preferred_relation] += 1
                        pref_top_relation_correct_count += 1
                    else:
                        pref_fp[predicted_relation] += 1
                        pref_fn[preferred_relation] += 1

        # Hierarchy-Aware Typed nDCG@10:
        if graded_relevance is not None and query_key in graded_relevance:
            query_gains = graded_relevance[query_key]
            if query_gains:
                h_ndcgs.append(hierarchy_aware_ndcg(ranked, query_gains, k))

    metrics: dict[str, float] = {
        "diagnostic_relation_aware_ndcg_at_10": mean(ndcgs),
        "diagnostic_mrr": mean(mrrs),
        "diagnostic_hits_at_1": mean(hits1),
        "diagnostic_hits_at_5": mean(hits5),
        "diagnostic_hits_at_10": mean(hits10),
        "diagnostic_map": mean(aps),
        "diagnostic_top1_relation_macro_f1": macro_f1(tp, fp, fn),
        "queries": float(len(answers)),
    }

    if preferred_pairs is not None:
        metrics["preferred_typed_mrr"] = mean(pref_rrs)
        metrics["preferred_typed_hits_at_1"] = mean(pref_hits1)
        metrics["preferred_typed_hits_at_5"] = mean(pref_hits5)
        metrics["preferred_typed_hits_at_10"] = mean(pref_hits10)
        metrics["preferred_typed_queries"] = float(len(pref_rrs))

        if pref_entity_correct_count > 0:
            metrics["preferred_entity_relation_accuracy"] = (
                pref_top_relation_correct_count / pref_entity_correct_count
            )
            metrics["preferred_entity_relation_macro_f1"] = macro_f1(
                pref_tp, pref_fp, pref_fn
            )
        else:
            metrics["preferred_entity_relation_accuracy"] = 0.0
            metrics["preferred_entity_relation_macro_f1"] = 0.0
        metrics["preferred_entity_relation_queries"] = float(pref_entity_correct_count)

    if graded_relevance is not None:
        metrics["hierarchy_aware_typed_ndcg_at_10"] = mean(h_ndcgs)
        metrics["hierarchy_aware_typed_ndcg_at_10_queries"] = float(len(h_ndcgs))

    return metrics


###
# File-level entry point
###


def score_files(
    predictions_path: str | Path,
    answers_path: str | Path,
    output_path: str | Path | None = None,
    preferred_path: str | Path | None = None,
    graded_path: str | Path | None = None,
    candidate_count: int | None = None,
    relations: tuple[str, ...] | list[str] = DEFAULT_RELATIONS,
    submission_format: str = "block",
    strict: bool = True,
) -> dict[str, float]:
    """
    Score a participant submission against a public answers/cands TSV.

    The default submission_format="block" matches the canonical
    participant contract: predictions are a 4-column TSV
    (SrcEntity, TgtEntity, Relation, Score) and per-query
    partitioning is recovered positionally from answers_path's row
    order. Use submission_format="row" when the predictions file
    already has a QueryID column (local scoring against train/valid
    splits where QueryID is available).

    Candidate-count handling
    ------------------------

    When candidate_count is omitted (None, the default), the
    scorer reads the first row of answers_path and uses
    len(TgtCandidates) as the per-query candidate count. This is
    correct for any release where every query has the same candidate
    cardinality — which is the canonical case (50 for the public
    release, 4 for the bundled mini fixture, 50 for the bundled
    canonical fixture). Pass an explicit integer when scoring against
    a fixture with mixed candidate counts or to enforce a specific
    expected value.

    Headline-metric discovery
    -------------------------

    Two optional files unlock the leaderboard metric families:

    * preferred_path -> preferred-pair metrics (paper §1.5 primary).
      Falls back to <answers_root>.preferred.tsv adjacent to the
      answers file.
    * graded_path -> Hierarchy-Aware Typed nDCG@10 (paper §1.5
      secondary). Falls back to <answers_root>.graded.tsv.

    The discovery rule strips .answers.tsv from the answers file
    when present; otherwise it strips the final extension. Each
    optional family is skipped (with a note on stderr) when neither
    explicit path nor fallback resolves to an existing file.

    The diagnostic metrics are always emitted. They use binary set
    membership against the full gold set and are NOT the leaderboard
    score; participants should report preferred_typed_* and
    hierarchy_aware_typed_* keys instead.
    """
    import sys

    from .hierarchy import load_graded_relevance

    answers_path = Path(answers_path)
    answers = load_answers(answers_path)

    if submission_format == "block":
        # Auto-detect candidate_count from the answers file if the
        # caller didn't pin one. Works because the canonical release
        # and all bundled fixtures use a uniform candidate count per
        # release (50 in the canonical release; 4 in the mini
        # fixture). Falls back to 50 only when the answers file is
        # empty (mostly to avoid a TypeError in the error path).
        if candidate_count is None:
            answer_rows_for_inspection = list(read_tsv(answers_path))
            if answer_rows_for_inspection:
                first = answer_rows_for_inspection[0]
                detected = len(parse_list(first.get("TgtCandidates", "[]")))
                candidate_count = detected if detected > 0 else 50
            else:
                candidate_count = 50
        predictions = load_block_format_predictions(
            predictions_path,
            answers_path,
            relations=relations,
            candidate_count=candidate_count,
            strict=strict,
        )
    elif submission_format == "row":
        # Round-trip path for prediction files that already carry an
        # explicit QueryID column. The participant-facing v0.2.0
        # submission format is "block"; this branch exists for kit
        # tests and for hand-authored prediction files used during
        # local development.
        predictions = read_tsv(predictions_path)
    else:
        raise ValueError(
            f"submission_format must be 'block' or 'row'; got "
            f"{submission_format!r}."
        )

    # Resolve the conventional sibling-path stem for the three
    # optional metric files. We strip .answers.tsv when present;
    # otherwise we drop the final suffix. This mirrors the organiser
    # release layout where NCIT-DOID.valid.answers.tsv has
    # siblings NCIT-DOID.valid.preferred.tsv, ...graded.tsv,
    # ...graded.tsv.
    name = answers_path.name
    if name.endswith(".answers.tsv"):
        stem = name[: -len(".answers.tsv")]
    elif name.endswith(".cands.tsv"):
        # Train/valid public cands.tsv with gold columns; the
        # convention places the gold pair file under
        # <task>.<split>.preferred.tsv next to the cands file. The
        # public release ships these under tasks/<TASK>/ next to
        # the cands TSV, while the kit example fixture puts them
        # under answers/<TASK>.<split>.... Both conventions are
        # tried below.
        stem = name[: -len(".cands.tsv")]
    else:
        stem = answers_path.stem

    def _sibling(suffix: str) -> Path | None:
        """Return the first existing sibling path matching suffix."""
        # Try directly next to the answers file first.
        cand = answers_path.with_name(stem + suffix)
        if cand.exists():
            return cand
        # The kit examples and the canonical release-private layout
        # place the metric files under an adjacent answers/ or
        # the same directory; both are covered by the with_name call
        # above. No further search to keep the discovery rule
        # predictable.
        return None

    preferred_pairs: dict[tuple[str, str], tuple[str, str]] | None = None
    if preferred_path is not None:
        cand = Path(preferred_path)
        if cand.exists():
            preferred_pairs = load_preferred_pairs(cand)
    else:
        cand = _sibling(".preferred.tsv")
        if cand is not None:
            preferred_pairs = load_preferred_pairs(cand)
    if preferred_pairs is None:
        print(
            "note: preferred-pair metrics skipped — no *.preferred.tsv file "
            "found alongside answers; reporting diagnostic metrics only. "
            "Leaderboard scores use the preferred-pair family (paper §1.5).",
            file=sys.stderr,
        )

    graded_relevance: dict[tuple[str, str], dict[tuple[str, str], float]] | None = None
    if graded_path is not None:
        cand = Path(graded_path)
        if cand.exists():
            graded_relevance = load_graded_relevance(cand)
    else:
        cand = _sibling(".graded.tsv")
        if cand is not None:
            graded_relevance = load_graded_relevance(cand)
    if graded_relevance is None:
        print(
            "note: Hierarchy-Aware Typed nDCG@10 skipped — no *.graded.tsv "
            "file found alongside answers. Build one with the "
            "`build-graded-relevance` CLI helper, or use the public release "
            "which ships pre-computed graded relevance.",
            file=sys.stderr,
        )

    metrics = score_prediction_rows(
        predictions,
        answers,
        preferred_pairs=preferred_pairs,
        graded_relevance=graded_relevance,
    )
    if output_path:
        write_json(output_path, metrics)
    return metrics
