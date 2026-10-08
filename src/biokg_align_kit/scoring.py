"""
Scoring for BioKG-Align (kit 0.4.0).

The kit is the single scorer implementation: the platform scoring program and the
organiser's private test scoring call the functions below, so a score computed
locally with the kit is computed by the same code as the leaderboard.

Query identity
--------------
Every query has an opaque identifier ``<task>-<8 lowercase hex>`` (for example
``SNOMED-FMA-3fa94c0e``). Candidate files carry ``QueryID, SrcEntity,
TgtCandidates``; evaluation files (answers, preferred, graded, query metadata) are
keyed by the same ``QueryID``. A source can own two queries (one equivalence-mode,
one subsumption-mode) with different pools, so ``SrcEntity`` alone is never a key.

Submissions
-----------
One five-column TSV for all tasks: ``QueryID  SrcEntity  TgtEntity  Relation
Score``, joined to the queries by ``QueryID``; row order is irrelevant. Every query
must carry exactly ``|candidates| x |relations|`` rows. :func:`load_submission`
enforces eight rules:

1. the header is exactly the five columns (and every row has five fields);
2. every ``QueryID`` exists in the query index, and every indexed query appears;
3. ``SrcEntity`` equals the query's source;
4. ``TgtEntity`` is in the query's candidate set;
5. ``Relation`` is one of the three relations;
6. ``Score`` parses to a finite float;
7. no duplicate ``(QueryID, TgtEntity, Relation)``;
8. every query has exactly ``|candidates| x |relations|`` rows.

**Strict mode** (``strict=True``; ``verify``, the platform scorer and the
organiser's private test scoring) makes every rule fatal. **Lenient mode**
(``strict=False``; only the kit's ``score`` command for train/valid) keeps rule 1
fatal and turns every other rule into a warning with a defined fallback: invalid
rows are dropped, duplicate pairs keep their maximum score, missing pairs of a
present query are filled with ``-inf`` (last rank), and queries without a valid row
are skipped. Lenient numbers are not leaderboard-comparable.

Metric families (key names shared with the organiser)
-----------------------------------------------------
* ``preferred_typed_mrr`` (primary), ``preferred_typed_hits_at_{1,5,10}``,
  ``median_preferred_typed_rank``, ``preferred_typed_queries``;
* ``preferred_entity_relation_{accuracy,macro_f1,queries}`` — relation typing on
  the queries whose top-ranked entity is the preferred target (gated);
* ``hierarchy_aware_typed_ndcg_at_10`` (secondary) and its ``_queries`` count;
* ``diagnostic_*`` set-based metrics (not the leaderboard score); ``queries``.

Ranking within a query is by ``(-Score, TgtEntity, relation order)`` with the
relation order ``equivalent < source_subsumed_by_target < source_subsumes_target``.
"""

from __future__ import annotations

import csv
import math
import re
import statistics
import warnings
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .io import parse_list, read_tsv, write_json


# Explicit relation ordering for ranking tie-breaks.
RELATION_TIEBREAK_ORDER: dict[str, int] = {
    "equivalent": 0,
    "source_subsumed_by_target": 1,
    "source_subsumes_target": 2,
}

DEFAULT_RELATIONS: tuple[str, ...] = (
    "equivalent",
    "source_subsumed_by_target",
    "source_subsumes_target",
)

QUERY_ID_PATTERN = re.compile(r"^(?P<task>[A-Z]+-[A-Z]+)-(?P<hex>[0-9a-f]{8})$")
SUBMISSION_COLUMNS: tuple[str, ...] = ("QueryID", "SrcEntity", "TgtEntity", "Relation", "Score")
QUERY_METADATA_COLUMNS: tuple[str, ...] = ("QueryID", "Task", "Split", "SrcEntity", "QueryMode")
MAX_EXAMPLES_PER_RULE = 50

_UNKNOWN_RELATION_RANK = len(RELATION_TIEBREAK_ORDER)

# Submission rule keys, in rule order. `malformed_row` (a row without five fields)
# belongs to rule 1 together with the header check.
SUBMISSION_RULES: tuple[str, ...] = (
    "malformed_row",
    "unknown_query_id",
    "missing_query",
    "source_mismatch",
    "off_pool_target",
    "bad_relation",
    "bad_score",
    "duplicate_pair",
    "incomplete_query",
)


class SubmissionError(ValueError):
    """A submission violates the format; ``messages`` lists every violation."""

    def __init__(self, messages: Iterable[str]) -> None:
        self.messages = list(messages)
        super().__init__("\n".join(self.messages))


class EvaluationSetError(ValueError):
    """Reference (evaluation) files violate the release contract."""

    def __init__(self, messages: Iterable[str]) -> None:
        self.messages = list(messages)
        super().__init__("\n".join(self.messages))


class _RuleLog:
    """Violation counter with at most MAX_EXAMPLES_PER_RULE examples per rule."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.examples: dict[str, list[str]] = defaultdict(list)

    def add(self, rule: str, message: str) -> None:
        self.counts[rule] += 1
        if len(self.examples[rule]) < MAX_EXAMPLES_PER_RULE:
            self.examples[rule].append(message)

    def __bool__(self) -> bool:
        return bool(self.counts)

    def messages(self) -> list[str]:
        out: list[str] = []
        for rule in sorted(self.counts, key=_rule_position):
            count = self.counts[rule]
            shown = self.examples[rule]
            more = f" (first {len(shown)} shown)" if count > len(shown) else ""
            out.append(f"[{rule}] {count} violation(s){more}:")
            out.extend(f"  - {example}" for example in shown)
        return out


def _rule_position(rule: str) -> int:
    return SUBMISSION_RULES.index(rule) if rule in SUBMISSION_RULES else len(SUBMISSION_RULES)


# =========================================================================
# Query index and evaluation-file loaders
# =========================================================================


@dataclass(frozen=True)
class Query:
    query_id: str
    task: str
    source: str
    candidates: frozenset[str]


QueryIndex = dict[str, Query]


def query_id_task(query_id: str) -> str | None:
    """Task prefix of a well-formed QueryID, or None."""
    match = QUERY_ID_PATTERN.match(query_id)
    return match.group("task") if match else None


def _require_columns(path: Path, rows_header: list[str] | None, required: Iterable[str]) -> None:
    missing = [column for column in required if column not in (rows_header or [])]
    if missing:
        raise EvaluationSetError([f"{path}: missing column(s) {missing}; header is {rows_header!r}"])


def _read_header_and_rows(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with Path(path).open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def load_query_index(paths: Iterable[str | Path]) -> QueryIndex:
    """
    Build the query index from candidate or answers files.

    Each file must sit in a directory named after its task
    (``tasks/<task>/<split>.cands.tsv`` or ``evaluation/<task>/<split>.answers.tsv``);
    every ``QueryID`` must match ``<task>-<8 hex>`` with that task. Duplicate IDs
    (across all files), empty candidate lists and duplicate candidates raise
    :class:`EvaluationSetError`.
    """
    index: QueryIndex = {}
    log = _RuleLog()
    for raw_path in paths:
        path = Path(raw_path)
        header, rows = _read_header_and_rows(path)
        _require_columns(path, header, ("QueryID", "SrcEntity", "TgtCandidates"))
        task_dir = path.parent.name
        for line, row in enumerate(rows, start=2):
            query_id = row.get("QueryID") or ""
            task = query_id_task(query_id)
            where = f"{path}:{line}"
            if task is None:
                log.add("query_id_format", f"{where}: QueryID {query_id!r} does not match <TASK>-<8 hex>")
                continue
            if task != task_dir:
                log.add("query_id_task", f"{where}: QueryID {query_id!r} has task prefix {task!r} but the file is under {task_dir!r}")
                continue
            if query_id in index:
                log.add("duplicate_query_id", f"{where}: duplicate QueryID {query_id!r}")
                continue
            source = row.get("SrcEntity") or ""
            if not source:
                log.add("empty_source", f"{where}: QueryID {query_id!r} has an empty SrcEntity")
                continue
            candidates = parse_list(row.get("TgtCandidates", ""))
            if not candidates:
                log.add("empty_candidates", f"{where}: QueryID {query_id!r} has no candidates")
                continue
            if len(set(candidates)) != len(candidates):
                log.add("duplicate_candidates", f"{where}: QueryID {query_id!r} lists a candidate more than once")
                continue
            index[query_id] = Query(query_id, task, source, frozenset(candidates))
    if log:
        raise EvaluationSetError(log.messages())
    return index


def load_answers(path: str | Path) -> dict[str, set[tuple[str, str]]]:
    """QueryID -> gold (TgtEntity, Relation) pairs, from an answers file
    (``QueryID SrcEntity TgtEntities Relations TgtCandidates``)."""
    path = Path(path)
    header, rows = _read_header_and_rows(path)
    _require_columns(path, header, ("QueryID", "TgtEntities", "Relations"))
    answers: dict[str, set[tuple[str, str]]] = {}
    log = _RuleLog()
    for line, row in enumerate(rows, start=2):
        query_id = row["QueryID"]
        targets = parse_list(row["TgtEntities"])
        relations = parse_list(row["Relations"])
        if query_id in answers:
            log.add("duplicate_query_id", f"{path}:{line}: duplicate QueryID {query_id!r}")
            continue
        if len(targets) != len(relations) or not targets:
            log.add("malformed_gold", f"{path}:{line}: {query_id!r} has {len(targets)} targets and {len(relations)} relations")
            continue
        answers[query_id] = set(zip(targets, relations))
    if log:
        raise EvaluationSetError(log.messages())
    return answers


def load_preferred_pairs(path: str | Path) -> dict[str, tuple[str, str]]:
    """QueryID -> the single preferred (TgtEntity, Relation). Raises on a
    missing file, a duplicate QueryID or a malformed row."""
    path = Path(path)
    header, rows = _read_header_and_rows(path)
    _require_columns(path, header, ("QueryID", "SrcEntity", "TgtEntity", "Relation"))
    preferred: dict[str, tuple[str, str]] = {}
    log = _RuleLog()
    for line, row in enumerate(rows, start=2):
        query_id = row.get("QueryID") or ""
        target = row.get("TgtEntity") or ""
        relation = row.get("Relation") or ""
        if not query_id or not target or not relation:
            log.add("malformed_preferred", f"{path}:{line}: empty QueryID, TgtEntity or Relation")
            continue
        if query_id in preferred:
            log.add(
                "duplicate_preferred",
                f"{path}:{line}: second preferred pair for {query_id!r}; the release "
                "contract requires exactly one preferred typed answer per query",
            )
            continue
        preferred[query_id] = (target, relation)
    if log:
        raise EvaluationSetError(log.messages())
    return preferred


def load_graded_relevance(path: str | Path) -> dict[str, dict[tuple[str, str], float]]:
    """QueryID -> {(TgtEntity, Relation): gain}. Raises on a duplicate
    (QueryID, TgtEntity, Relation), an unparseable or a non-finite gain."""
    path = Path(path)
    header, rows = _read_header_and_rows(path)
    _require_columns(path, header, ("QueryID", "TgtEntity", "Relation", "Gain"))
    graded: dict[str, dict[tuple[str, str], float]] = defaultdict(dict)
    log = _RuleLog()
    for line, row in enumerate(rows, start=2):
        query_id = row["QueryID"]
        key = (row["TgtEntity"], row["Relation"])
        try:
            gain = float(row["Gain"])
        except (TypeError, ValueError):
            log.add("bad_gain", f"{path}:{line}: gain {row['Gain']!r} is not a number")
            continue
        if not math.isfinite(gain):
            log.add("bad_gain", f"{path}:{line}: gain {row['Gain']!r} is not finite")
            continue
        if key in graded[query_id]:
            log.add("duplicate_graded_pair", f"{path}:{line}: duplicate graded pair {query_id!r} {key!r}")
            continue
        graded[query_id][key] = gain
    if log:
        raise EvaluationSetError(log.messages())
    return dict(graded)


def load_query_metadata(path: str | Path) -> dict[str, dict[str, str]]:
    """QueryID -> {Task, Split, SrcEntity, QueryMode}. Raises on duplicates."""
    path = Path(path)
    header, rows = _read_header_and_rows(path)
    _require_columns(path, header, QUERY_METADATA_COLUMNS)
    metadata: dict[str, dict[str, str]] = {}
    log = _RuleLog()
    for line, row in enumerate(rows, start=2):
        query_id = row["QueryID"]
        if query_id in metadata:
            log.add("duplicate_metadata", f"{path}:{line}: duplicate query_metadata row for {query_id!r}")
            continue
        metadata[query_id] = {column: row[column] for column in QUERY_METADATA_COLUMNS}
    if log:
        raise EvaluationSetError(log.messages())
    return metadata


# =========================================================================
# Submission loader (the single strict/lenient loader)
# =========================================================================


@dataclass
class SubmissionDiagnostics:
    queries_expected: int
    queries_scored: int
    dropped_rows_by_rule: dict[str, int] = field(default_factory=dict)
    filled_pairs: int = 0
    duplicate_pairs: int = 0
    warnings: list[str] = field(default_factory=list)
    # Violation count attributed to each task (rows dropped, duplicates merged,
    # pairs filled, queries skipped); reported per task as `submission_warnings`.
    violations_by_task: dict[str, int] = field(default_factory=dict)
    # Violations no scored task can own: malformed rows, and unknown QueryIDs whose
    # task prefix is missing or not among the scored tasks. Reported in the macro
    # block as `submission_warnings_unattributed` (and in `submission_warnings_total`).
    unattributed_violations: int = 0


@dataclass
class LoadedSubmission:
    predictions_by_query: dict[str, list[dict]]
    diagnostics: SubmissionDiagnostics


def load_submission(
    path: str | Path,
    index: QueryIndex,
    relations: tuple[str, ...] | list[str] = DEFAULT_RELATIONS,
    strict: bool = True,
) -> LoadedSubmission:
    """
    Load a five-column submission and join it to ``index`` by ``QueryID``.

    Returns complete per-query prediction lists (``|candidates| x |relations|``
    rows each, ``Score`` as float) for every present query. In strict mode any
    violation of rules 2–8 raises :class:`SubmissionError` listing every rule
    with up to 50 examples; in lenient mode they become warnings with the
    documented fallback. Rule 1 (header) raises in both modes, and so does a
    submission in which no query of a non-empty index has a valid row.
    """
    relations = tuple(relations)
    relation_set = set(relations)
    path = Path(path)
    log = _RuleLog()
    by_task: Counter[str] = Counter()
    pairs_by_query: dict[str, dict[tuple[str, str], float]] = {}

    with path.open("r", encoding="utf-8", newline="") as handle:
        header = tuple(handle.readline().rstrip("\r\n").split("\t"))
        if header != SUBMISSION_COLUMNS:
            raise SubmissionError([
                f"[header] submission header is {list(header)!r}; the v0.4.0 format "
                f"requires exactly {list(SUBMISSION_COLUMNS)!r} (tab-separated, in this "
                "order). The positional four-column block format of v0.2–v0.3 is no "
                "longer accepted."
            ])
        reader = csv.reader(handle, delimiter="\t")
        for line, fields in enumerate(reader, start=2):
            if len(fields) != len(SUBMISSION_COLUMNS):
                log.add("malformed_row", f"line {line}: {len(fields)} field(s), expected {len(SUBMISSION_COLUMNS)}")
                by_task[""] += 1
                continue
            query_id, source, target, relation, score_text = fields
            query = index.get(query_id)
            if query is None:
                log.add("unknown_query_id", f"line {line}: unknown QueryID {query_id!r}")
                by_task[query_id_task(query_id) or ""] += 1
                continue
            if source != query.source:
                log.add("source_mismatch", f"line {line}: {query_id} SrcEntity {source!r} != {query.source!r}")
                by_task[query.task] += 1
                continue
            if target not in query.candidates:
                log.add("off_pool_target", f"line {line}: {query_id} TgtEntity {target!r} is not a candidate of this query")
                by_task[query.task] += 1
                continue
            if relation not in relation_set:
                log.add("bad_relation", f"line {line}: {query_id} Relation {relation!r} not in {list(relations)}")
                by_task[query.task] += 1
                continue
            try:
                score = float(score_text)
            except ValueError:
                score = math.nan
            if not math.isfinite(score):
                log.add("bad_score", f"line {line}: {query_id} Score {score_text!r} is not a finite float")
                by_task[query.task] += 1
                continue
            pairs = pairs_by_query.setdefault(query_id, {})
            key = (target, relation)
            if key in pairs:
                log.add("duplicate_pair", f"line {line}: {query_id} duplicate pair ({target}, {relation})")
                by_task[query.task] += 1
                if score > pairs[key]:
                    pairs[key] = score
                continue
            pairs[key] = score

    for query_id, query in index.items():
        if query_id not in pairs_by_query:
            log.add("missing_query", f"{query_id} (source {query.source}) has no valid row")
            by_task[query.task] += 1

    filled = 0
    predictions_by_query: dict[str, list[dict]] = {}
    for query_id, pairs in pairs_by_query.items():
        query = index[query_id]
        expected = len(query.candidates) * len(relations)
        missing = expected - len(pairs)
        if missing:
            log.add("incomplete_query", f"{query_id}: {len(pairs)} of {expected} (candidate, relation) pairs")
            by_task[query.task] += missing
            filled += missing
        rows = []
        for target in sorted(query.candidates):
            for relation in relations:
                rows.append({
                    "QueryID": query_id,
                    "SrcEntity": query.source,
                    "TgtEntity": target,
                    "Relation": relation,
                    "Score": pairs.get((target, relation), -math.inf),
                })
        predictions_by_query[query_id] = rows

    if strict and log:
        raise SubmissionError(log.messages())

    warning_messages: list[str] = []
    if log:
        warning_messages = log.messages()
        summary = (
            f"{path.name}: lenient scoring applied fallbacks to "
            f"{sum(log.counts.values())} violation(s) "
            f"({', '.join(f'{rule}={count}' for rule, count in sorted(log.counts.items(), key=lambda item: _rule_position(item[0])))}); "
            "these numbers are not leaderboard-comparable"
        )
        warnings.warn(summary + "\n" + "\n".join(warning_messages), UserWarning, stacklevel=2)
    if index and not predictions_by_query:
        raise SubmissionError(
            ["[no_scored_queries] no query has a valid row; nothing can be scored"]
            + warning_messages
        )

    dropped = {
        rule: count
        for rule, count in log.counts.items()
        if rule in {"malformed_row", "unknown_query_id", "source_mismatch", "off_pool_target", "bad_relation", "bad_score", "duplicate_pair"}
    }
    index_tasks = {query.task for query in index.values()}
    diagnostics = SubmissionDiagnostics(
        queries_expected=len(index),
        queries_scored=len(predictions_by_query),
        dropped_rows_by_rule=dropped,
        filled_pairs=filled,
        duplicate_pairs=log.counts.get("duplicate_pair", 0),
        warnings=warning_messages,
        violations_by_task={task: count for task, count in by_task.items() if task in index_tasks},
        unattributed_violations=sum(count for task, count in by_task.items() if task not in index_tasks),
    )
    return LoadedSubmission(predictions_by_query=predictions_by_query, diagnostics=diagnostics)


def predictions_from_rows(rows: Iterable[dict]) -> dict[str, list[dict]]:
    """Group already-validated in-memory prediction rows by QueryID (scores as
    floats). For organiser tooling that scores rows it produced itself; files
    from anywhere else go through :func:`load_submission`."""
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[row["QueryID"]].append({**row, "Score": float(row["Score"])})
    return dict(grouped)


# =========================================================================
# Metrics
# =========================================================================


def rank_key(row: dict) -> tuple[float, str, int]:
    """Ranking order within a query: descending score, then TgtEntity, then
    the explicit relation order."""
    return (
        -float(row["Score"]),
        row["TgtEntity"],
        RELATION_TIEBREAK_ORDER.get(row["Relation"], _UNKNOWN_RELATION_RANK),
    )


def rank_query(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=rank_key)


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


def score_prediction_rows(
    predictions_by_query: dict[str, list[dict]],
    answers: dict[str, set[tuple[str, str]]],
    preferred_pairs: dict[str, tuple[str, str]],
    graded_relevance: dict[str, dict[tuple[str, str], float]],
    k: int = 10,
) -> dict[str, float]:
    """Metrics over the queries in ``answers`` (all inputs keyed by QueryID)."""
    from .hierarchy import hierarchy_aware_ndcg

    ndcgs: list[float] = []
    mrrs: list[float] = []
    hits1: list[float] = []
    hits5: list[float] = []
    hits10: list[float] = []
    aps: list[float] = []
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)

    pref_rrs: list[float] = []
    pref_hits1: list[float] = []
    pref_hits5: list[float] = []
    pref_hits10: list[float] = []
    pref_ranks: list[float] = []
    gated_tp: dict[str, int] = defaultdict(int)
    gated_fp: dict[str, int] = defaultdict(int)
    gated_fn: dict[str, int] = defaultdict(int)
    gated_count = 0
    gated_correct = 0

    h_ndcgs: list[float] = []

    for query_id in sorted(answers):
        gold = answers[query_id]
        ranked = rank_query(predictions_by_query.get(query_id, []))

        # diagnostic (set-based) metrics
        relevance = [1 if (row["TgtEntity"], row["Relation"]) in gold else 0 for row in ranked]
        ndcgs.append(ndcg(relevance, k, ideal_count=len(gold)))
        mrrs.append(reciprocal_rank(relevance))
        hits1.append(1.0 if any(relevance[:1]) else 0.0)
        hits5.append(1.0 if any(relevance[:5]) else 0.0)
        hits10.append(1.0 if any(relevance[:10]) else 0.0)
        aps.append(average_precision(relevance, len(gold)))
        predicted_positive = {(row["TgtEntity"], row["Relation"]) for row in ranked[:1]}
        for pair in predicted_positive:
            if pair in gold:
                tp[pair[1]] += 1
            else:
                fp[pair[1]] += 1
        for pair in gold - predicted_positive:
            fn[pair[1]] += 1

        preferred = preferred_pairs[query_id]
        rank = next(
            (position for position, row in enumerate(ranked, start=1) if (row["TgtEntity"], row["Relation"]) == preferred),
            None,
        )
        if rank is None:
            pref_rrs.append(0.0)
            pref_hits1.append(0.0)
            pref_hits5.append(0.0)
            pref_hits10.append(0.0)
            pref_ranks.append(float(len(ranked) + 1))
        else:
            pref_rrs.append(1.0 / rank)
            pref_hits1.append(1.0 if rank <= 1 else 0.0)
            pref_hits5.append(1.0 if rank <= 5 else 0.0)
            pref_hits10.append(1.0 if rank <= 10 else 0.0)
            pref_ranks.append(float(rank))

        # Relation typing on the preferred entity, gated: a query counts only when
        # its top-ranked entity (per-entity max score, entity-id tie-break) IS the
        # preferred target. The top row of the ranking is exactly that entity's
        # best relation, so its relation is the system's relation prediction.
        if ranked and ranked[0]["TgtEntity"] == preferred[0]:
            gated_count += 1
            predicted_relation = ranked[0]["Relation"]
            if predicted_relation == preferred[1]:
                gated_correct += 1
                gated_tp[preferred[1]] += 1
            else:
                gated_fp[predicted_relation] += 1
                gated_fn[preferred[1]] += 1

        query_gains = graded_relevance.get(query_id)
        if query_gains:
            h_ndcgs.append(hierarchy_aware_ndcg(ranked, query_gains, k))

    return {
        "diagnostic_relation_aware_ndcg_at_10": mean(ndcgs),
        "diagnostic_mrr": mean(mrrs),
        "diagnostic_hits_at_1": mean(hits1),
        "diagnostic_hits_at_5": mean(hits5),
        "diagnostic_hits_at_10": mean(hits10),
        "diagnostic_map": mean(aps),
        "diagnostic_top1_relation_macro_f1": macro_f1(tp, fp, fn),
        "queries": float(len(answers)),
        "preferred_typed_mrr": mean(pref_rrs),
        "preferred_typed_hits_at_1": mean(pref_hits1),
        "preferred_typed_hits_at_5": mean(pref_hits5),
        "preferred_typed_hits_at_10": mean(pref_hits10),
        "median_preferred_typed_rank": float(statistics.median(pref_ranks)) if pref_ranks else 0.0,
        "preferred_typed_queries": float(len(pref_rrs)),
        "preferred_entity_relation_accuracy": gated_correct / gated_count if gated_count else 0.0,
        "preferred_entity_relation_macro_f1": macro_f1(gated_tp, gated_fp, gated_fn) if gated_count else 0.0,
        "preferred_entity_relation_queries": float(gated_count),
        "hierarchy_aware_typed_ndcg_at_10": mean(h_ndcgs),
        "hierarchy_aware_typed_ndcg_at_10_queries": float(len(h_ndcgs)),
    }


# =========================================================================
# Evaluation sets, tasks, submissions
# =========================================================================


@dataclass
class EvaluationSet:
    """The reference files of one (task, split), loaded and validated."""

    task: str
    split: str
    index: QueryIndex
    answers: dict[str, set[tuple[str, str]]]
    preferred: dict[str, tuple[str, str]]
    graded: dict[str, dict[tuple[str, str], float]]
    metadata: dict[str, dict[str, str]]


def evaluation_paths(evaluation_dir: str | Path, task: str, split: str) -> dict[str, Path]:
    """Sibling discovery: ``evaluation/<task>/<split>.answers.tsv`` ->
    ``<split>.preferred.tsv`` and ``<split>.graded.tsv`` in the same directory,
    plus ``evaluation/query_metadata.tsv``. Nothing else."""
    root = Path(evaluation_dir)
    task_dir = root / task
    return {
        "answers": task_dir / f"{split}.answers.tsv",
        "preferred": task_dir / f"{split}.preferred.tsv",
        "graded": task_dir / f"{split}.graded.tsv",
        "metadata": root / "query_metadata.tsv",
    }


def load_evaluation_set(
    evaluation_dir: str | Path,
    task: str,
    split: str,
    relations: tuple[str, ...] = DEFAULT_RELATIONS,
) -> EvaluationSet:
    """Load one (task, split)'s reference files and validate them in full with
    the shared validator; raises :class:`EvaluationSetError` naming the IDs."""
    from .evaluation import validate_evaluation_set

    paths = evaluation_paths(evaluation_dir, task, split)
    missing = [str(path) for path in paths.values() if not path.is_file()]
    if missing:
        raise EvaluationSetError([f"missing evaluation file(s) for {task}/{split}: {missing}"])
    index = load_query_index([paths["answers"]])
    answers = load_answers(paths["answers"])
    preferred = load_preferred_pairs(paths["preferred"])
    graded = load_graded_relevance(paths["graded"])
    metadata = {
        query_id: row
        for query_id, row in load_query_metadata(paths["metadata"]).items()
        if row["Task"] == task and row["Split"] == split
    }
    errors = validate_evaluation_set(index, answers, preferred, graded, metadata, relations)
    if errors:
        raise EvaluationSetError([f"{task}/{split}: evaluation set is invalid"] + errors)
    return EvaluationSet(task, split, index, answers, preferred, graded, metadata)


def score_task(
    loaded: LoadedSubmission,
    evaluation_dir: str | Path,
    task: str,
    split: str,
    strict: bool = True,
) -> dict[str, float]:
    """
    Score one task of a loaded submission against ``evaluation_dir``.

    The reference files are validated in full before scoring is restricted to the
    queries present in the submission. Strict mode requires every query of the
    task; lenient results carry ``queries_expected``, ``queries_scored`` and
    ``submission_warnings``, and a task without any scored query gets only those
    three counts.
    """
    evaluation = load_evaluation_set(evaluation_dir, task, split)
    return score_evaluation_set(loaded, evaluation, strict=strict)


def score_evaluation_set(
    loaded: LoadedSubmission,
    evaluation: EvaluationSet,
    strict: bool = True,
) -> dict[str, float]:
    present = [query_id for query_id in sorted(evaluation.index) if query_id in loaded.predictions_by_query]
    log = _RuleLog()
    for query_id in present:
        query = evaluation.index[query_id]
        rows = loaded.predictions_by_query[query_id]
        if {row["TgtEntity"] for row in rows} != set(query.candidates) or any(
            row["SrcEntity"] != query.source for row in rows
        ):
            log.add("index_mismatch", f"{query_id}: the submission was joined against a different pool/source than the evaluation set")
    if log:
        raise EvaluationSetError(log.messages())
    if strict and len(present) != len(evaluation.index):
        missing = [query_id for query_id in sorted(evaluation.index) if query_id not in loaded.predictions_by_query]
        raise SubmissionError([f"[missing_query] {len(missing)} {evaluation.task}/{evaluation.split} query(ies) absent, e.g. {missing[:5]}"])

    violations = float(loaded.diagnostics.violations_by_task.get(evaluation.task, 0))
    if not present:
        return {
            "queries_expected": float(len(evaluation.index)),
            "queries_scored": 0.0,
            "submission_warnings": violations,
        }
    answers = {query_id: evaluation.answers[query_id] for query_id in present}
    preferred = {query_id: evaluation.preferred[query_id] for query_id in present}
    graded = {query_id: evaluation.graded[query_id] for query_id in present if query_id in evaluation.graded}
    metrics = score_prediction_rows(loaded.predictions_by_query, answers, preferred, graded)
    if not strict:
        metrics["queries_expected"] = float(len(evaluation.index))
        metrics["queries_scored"] = float(len(present))
        metrics["submission_warnings"] = violations
    return metrics


def task_names(root: str | Path, filename: str) -> list[str]:
    """Task directories under ``root`` that contain ``filename``."""
    return sorted(path.name for path in Path(root).iterdir() if path.is_dir() and (path / filename).is_file())


def score_submission(
    submission_path: str | Path,
    data_dir: str | Path | None,
    evaluation_dir: str | Path | None,
    split: str,
    tasks: Iterable[str] | None = None,
    strict: bool = True,
) -> dict[str, dict[str, float]]:
    """
    Score a combined-task submission. The query index comes from
    ``data_dir/tasks/<task>/<split>.cands.tsv`` (or, when ``data_dir`` is None,
    from ``evaluation_dir/<task>/<split>.answers.tsv``); the reference files from
    ``evaluation_dir`` (default ``data_dir/evaluation``). Returns per-task metrics
    plus ``"macro"`` (:func:`macro_average_tasks`).
    """
    if evaluation_dir is None:
        if data_dir is None:
            raise ValueError("score_submission needs data_dir or evaluation_dir")
        evaluation_dir = Path(data_dir) / "evaluation"
    evaluation_dir = Path(evaluation_dir)
    if data_dir is not None:
        tasks_root, filename = Path(data_dir) / "tasks", f"{split}.cands.tsv"
    else:
        tasks_root, filename = evaluation_dir, f"{split}.answers.tsv"
    selected = sorted(tasks) if tasks else task_names(tasks_root, filename)
    if not selected:
        raise ValueError(f"no task under {tasks_root} has {filename}")
    index = load_query_index([tasks_root / task / filename for task in selected])
    loaded = load_submission(submission_path, index, strict=strict)
    per_task = {task: score_task(loaded, evaluation_dir, task, split, strict=strict) for task in selected}
    return submission_result(per_task, loaded)


def submission_result(per_task: dict[str, dict[str, float]], loaded: LoadedSubmission) -> dict[str, dict[str, float]]:
    """Per-task metrics plus the ``"macro"`` block (:func:`macro_average_tasks`) with the
    submission-level counts no task owns: ``submission_warnings_unattributed`` and
    ``submission_warnings_total`` (per-task warnings + unattributed). Every scoring entry
    point builds its result here."""
    result: dict[str, dict[str, float]] = dict(per_task)
    macro = macro_average_tasks(per_task)
    unattributed = float(loaded.diagnostics.unattributed_violations)
    macro["submission_warnings_unattributed"] = unattributed
    macro["submission_warnings_total"] = macro.get("submission_warnings", 0.0) + unattributed
    result["macro"] = macro
    return result


# =========================================================================
# Cross-task reduction
# =========================================================================

_COUNT_SUFFIXES = ("_queries", "_count", "_scored", "_expected", "_warnings")


def is_count_metric(key: str) -> bool:
    """The shared count-metric registry: a key is a count iff it is exactly
    ``queries`` or ends in ``_queries``, ``_count``, ``_scored``, ``_expected``
    or ``_warnings``. Counts are summed across tasks; everything else is a rate
    and is averaged."""
    return key == "queries" or key.endswith(_COUNT_SUFFIXES)


def _task_was_scored(metrics: dict[str, float]) -> bool:
    """A task contributes rate metrics iff it scored at least one query; an entry
    without either count (hand-built metric dicts) is taken as scored."""
    if "queries_scored" in metrics:
        return metrics["queries_scored"] > 0
    if "queries" in metrics:
        return metrics["queries"] > 0
    return True


def macro_average_tasks(per_task: dict[str, dict[str, float]]) -> dict[str, float]:
    """
    Macro over tasks. Rate metrics: arithmetic mean over the tasks with at least
    one scored query. Count metrics (:func:`is_count_metric`): summed over every
    requested task, emitted as ``<key>_sum``, ``<key>_mean`` and the bare key
    (= the sum). Also reports ``tasks`` and ``tasks_scored``.
    """
    scored = [metrics for metrics in per_task.values() if _task_was_scored(metrics)]
    keys: set[str] = set()
    for metrics in per_task.values():
        keys.update(metrics)
    macro: dict[str, float] = {}
    for key in sorted(keys):
        if is_count_metric(key):
            values = [float(metrics[key]) for metrics in per_task.values() if key in metrics]
            total = float(sum(values))
            macro[f"{key}_sum"] = total
            macro[f"{key}_mean"] = total / len(values)
            macro[key] = total
        else:
            values = [float(metrics[key]) for metrics in scored if key in metrics]
            if values:
                macro[key] = sum(values) / len(values)
    macro["tasks"] = float(len(per_task))
    macro["tasks_scored"] = float(len(scored))
    return macro


def write_metrics(path: str | Path, metrics: dict) -> None:
    write_json(path, metrics)
