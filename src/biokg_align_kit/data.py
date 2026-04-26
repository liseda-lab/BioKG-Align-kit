from __future__ import annotations

import csv
from pathlib import Path

from .io import parse_list, read_tsv


def summarize_data(data_dir: str | Path) -> dict[str, object]:
    root = Path(data_dir)
    summary: dict[str, object] = {"data_dir": str(root), "tasks": {}}
    properties_path = root / "graph" / "properties.csv"
    triples_path = root / "graph" / "triples.csv"
    if properties_path.exists():
        with properties_path.open("r", encoding="utf-8", newline="") as handle:
            summary["nodes"] = sum(1 for _ in csv.DictReader(handle))
    if triples_path.exists():
        with triples_path.open("r", encoding="utf-8", newline="") as handle:
            summary["triples"] = sum(1 for _ in csv.DictReader(handle))
    tasks: dict[str, object] = {}
    for task_dir in sorted((root / "tasks").glob("*")):
        if not task_dir.is_dir():
            continue
        split_summary = {}
        for split in ["train", "valid", "test"]:
            path = task_dir / f"{split}.cands.tsv"
            if not path.exists():
                continue
            rows = read_tsv(path)
            counts = [len(parse_list(row["TgtCandidates"])) for row in rows]
            split_summary[split] = {
                "queries": len(rows),
                "candidates_min": min(counts) if counts else 0,
                "candidates_max": max(counts) if counts else 0,
            }
        tasks[task_dir.name] = split_summary
    summary["tasks"] = tasks
    return summary
