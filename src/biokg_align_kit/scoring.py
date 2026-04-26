from __future__ import annotations

import math
from collections import defaultdict
from pathlib import Path

from .io import parse_list, read_tsv, write_json


def load_answers(path: str | Path) -> dict[str, set[tuple[str, str]]]:
    answers: dict[str, set[tuple[str, str]]] = defaultdict(set)
    for row in read_tsv(path):
        targets = parse_list(row["TgtEntities"]) if row.get("TgtEntities", "").startswith("[") else [row["TgtEntity"]]
        relations = parse_list(row["Relations"]) if row.get("Relations", "").startswith("[") else [row["Relation"]]
        if len(targets) != len(relations):
            raise ValueError(f"Answer target/relation list length mismatch for {row['SrcEntity']}")
        for target, relation in zip(targets, relations):
            answers[row["SrcEntity"]].add((target, relation))
    return answers


def score_prediction_rows(
    predictions: list[dict[str, str]],
    answers: dict[str, set[tuple[str, str]]],
    k: int = 10,
) -> dict[str, float]:
    by_source: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in predictions:
        by_source[row["SrcEntity"]].append(row)

    ndcgs: list[float] = []
    mrrs: list[float] = []
    hits1: list[float] = []
    hits5: list[float] = []
    hits10: list[float] = []
    aps: list[float] = []
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)

    for source, gold in sorted(answers.items()):
        ranked = sorted(
            by_source.get(source, []),
            key=lambda row: (-float(row.get("Score", 0.0)), row["TgtEntity"], row["Relation"]),
        )
        relevance = [1 if (row["TgtEntity"], row["Relation"]) in gold else 0 for row in ranked]
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

    return {
        "relation_aware_ndcg_at_10": mean(ndcgs),
        "mrr": mean(mrrs),
        "hits_at_1": mean(hits1),
        "hits_at_5": mean(hits5),
        "hits_at_10": mean(hits10),
        "map": mean(aps),
        "macro_f1": macro_f1(tp, fp, fn),
        "queries": float(len(answers)),
    }


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


def score_files(predictions_path: str | Path, answers_path: str | Path, output_path: str | Path | None = None) -> dict[str, float]:
    metrics = score_prediction_rows(read_tsv(predictions_path), load_answers(answers_path))
    if output_path:
        write_json(output_path, metrics)
    return metrics
