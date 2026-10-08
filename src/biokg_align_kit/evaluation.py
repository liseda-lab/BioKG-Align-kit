"""
The shared evaluation-set validator.

``score_task`` calls :func:`validate_evaluation_set` before it scores anything, and
the organiser's ``validate_release`` calls the same function on every released
evaluation set, so scoring and release validation enforce identical rules. (The
loaders in :mod:`biokg_align_kit.scoring` already reject duplicate IDs, duplicate
preferred rows, duplicate graded pairs, non-finite gains, empty candidate lists and
duplicate candidates before anything reaches this function.)
"""

from __future__ import annotations

import math

from .preferred import query_mode
from .scoring import DEFAULT_RELATIONS, MAX_EXAMPLES_PER_RULE, QueryIndex


def validate_evaluation_set(
    index: QueryIndex,
    answers: dict[str, set[tuple[str, str]]],
    preferred: dict[str, tuple[str, str]],
    graded: dict[str, dict[tuple[str, str], float]],
    metadata: dict[str, dict[str, str]],
    relations: tuple[str, ...] = DEFAULT_RELATIONS,
) -> list[str]:
    """
    Return every violation (at most 50 examples per rule) of:

    * the answers IDs equal the index IDs;
    * every answers ID has exactly one preferred row whose pair is in its gold set,
      whose target is in its pool and whose relation is in ``relations``;
    * every graded row names a target in the pool and a relation in ``relations``,
      with a finite gain in (0, 1]; the preferred pair has gain 1.0;
    * exactly one query-metadata row per ID, with Task / SrcEntity matching the
      index and ``QueryMode`` consistent with the preferred relation;
    * preferred, graded and metadata IDs are subsets of the answers IDs.
    """
    relation_set = set(relations)
    errors: dict[str, list[str]] = {}
    counts: dict[str, int] = {}

    def add(rule: str, message: str) -> None:
        counts[rule] = counts.get(rule, 0) + 1
        bucket = errors.setdefault(rule, [])
        if len(bucket) < MAX_EXAMPLES_PER_RULE:
            bucket.append(message)

    answer_ids = set(answers)
    for query_id in sorted(answer_ids - set(index)):
        add("answers_not_in_index", f"{query_id}: answers row has no query in the index")
    for query_id in sorted(set(index) - answer_ids):
        add("index_without_answers", f"{query_id}: query has no answers row")

    for query_id in sorted(answer_ids & set(index)):
        query = index[query_id]
        pair = preferred.get(query_id)
        if pair is None:
            add("missing_preferred", f"{query_id}: no preferred pair")
            continue
        target, relation = pair
        if relation not in relation_set:
            add("preferred_bad_relation", f"{query_id}: preferred relation {relation!r} not in {list(relations)}")
        if pair not in answers[query_id]:
            add("preferred_not_gold", f"{query_id}: preferred pair {pair!r} is not in the query's gold set")
        if target not in query.candidates:
            add("preferred_off_pool", f"{query_id}: preferred target {target!r} is not in the query's pool")
        gains = graded.get(query_id, {})
        if gains.get(pair) != 1.0:
            add("preferred_gain_not_one", f"{query_id}: preferred pair {pair!r} has graded gain {gains.get(pair)!r}, expected 1.0")
        row = metadata.get(query_id)
        if row is None:
            add("missing_metadata", f"{query_id}: no query_metadata row")
        else:
            if relation in relation_set and row.get("QueryMode") != query_mode(relation):
                add(
                    "metadata_mode_mismatch",
                    f"{query_id}: QueryMode {row.get('QueryMode')!r} but the preferred relation is {relation!r}",
                )
            if row.get("Task") != query.task:
                add("metadata_task_mismatch", f"{query_id}: metadata Task {row.get('Task')!r} != {query.task!r}")
            if row.get("SrcEntity") != query.source:
                add("metadata_source_mismatch", f"{query_id}: metadata SrcEntity {row.get('SrcEntity')!r} != {query.source!r}")

    for query_id, gains in sorted(graded.items()):
        if query_id not in answer_ids:
            add("graded_not_in_answers", f"{query_id}: graded rows for a query without answers")
            continue
        query = index.get(query_id)
        for (target, relation), gain in sorted(gains.items()):
            if query is not None and target not in query.candidates:
                add("graded_off_pool", f"{query_id}: graded target {target!r} is not in the pool")
            if relation not in relation_set:
                add("graded_bad_relation", f"{query_id}: graded relation {relation!r} not in {list(relations)}")
            if not (math.isfinite(gain) and 0.0 < gain <= 1.0):
                add("graded_bad_gain", f"{query_id}: gain {gain!r} for ({target}, {relation}) is not in (0, 1]")

    for query_id in sorted(set(preferred) - answer_ids):
        add("preferred_not_in_answers", f"{query_id}: preferred row for a query without answers")
    for query_id in sorted(set(metadata) - answer_ids):
        add("metadata_not_in_answers", f"{query_id}: query_metadata row for a query without answers")

    messages: list[str] = []
    for rule in sorted(errors):
        shown = errors[rule]
        more = f" (first {len(shown)} shown)" if counts[rule] > len(shown) else ""
        messages.append(f"[{rule}] {counts[rule]} violation(s){more}:")
        messages.extend(f"  - {message}" for message in shown)
    return messages
