from __future__ import annotations

import random
from pathlib import Path

from .io import parse_list, read_tsv, write_tsv
from .text import lexical_score

RELATIONS = ["equivalent", "source_subsumed_by_target", "source_subsumes_target"]


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
    data_path = Path(data_dir)
    candidates = read_tsv(data_path / "tasks" / task / f"{split}.cands.tsv")
    properties = load_properties(data_path)
    rows = []
    for row in candidates:
        source_id = row["SrcEntity"]
        for target_id in parse_list(row["TgtCandidates"]):
            for relation in RELATIONS:
                rows.append(
                    {
                        "SrcEntity": source_id,
                        "TgtEntity": target_id,
                        "Relation": relation,
                        "Score": f"{score(source_id, target_id, relation, properties, baseline, seed):.8f}",
                    }
                )
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
    if baseline == "random":
        return random.Random(f"{seed}:{source_id}:{target_id}:{relation}").random()
    if baseline != "lexical":
        raise ValueError(f"Unsupported baseline: {baseline}")
    source = properties.get(source_id, {})
    target = properties.get(target_id, {})
    source_text = " ".join([source.get("preferred_label", ""), source.get("synonyms", "")])
    target_text = " ".join([target.get("preferred_label", ""), target.get("synonyms", "")])
    relation_bias = 0.05 if relation == "equivalent" else 0.0
    return lexical_score(source_text, target_text) + relation_bias
