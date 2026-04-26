from __future__ import annotations

from pathlib import Path

from .io import parse_list, read_tsv

RELATIONS = {"equivalent", "source_subsumed_by_target", "source_subsumes_target"}
REQUIRED_COLUMNS = {"SrcEntity", "TgtEntity", "Relation", "Score"}


def validate_submission(predictions_path: str | Path, candidates_path: str | Path) -> list[str]:
    errors: list[str] = []
    predictions = read_tsv(predictions_path)
    if predictions:
        missing = REQUIRED_COLUMNS - set(predictions[0])
        if missing:
            errors.append(f"Prediction file is missing columns: {sorted(missing)}")

    candidates = {
        row["SrcEntity"]: set(parse_list(row["TgtCandidates"]))
        for row in read_tsv(candidates_path)
    }
    seen = set()
    for index, row in enumerate(predictions, start=2):
        source = row.get("SrcEntity", "")
        target = row.get("TgtEntity", "")
        relation = row.get("Relation", "")
        key = (source, target, relation)
        if source not in candidates:
            errors.append(f"Line {index}: unknown source entity {source!r}")
        elif target not in candidates[source]:
            errors.append(f"Line {index}: target {target!r} is not a candidate for {source!r}")
        if relation not in RELATIONS:
            errors.append(f"Line {index}: unknown relation {relation!r}")
        try:
            float(row.get("Score", ""))
        except ValueError:
            errors.append(f"Line {index}: score is not numeric")
        if key in seen:
            errors.append(f"Line {index}: duplicate prediction for {key}")
        seen.add(key)
    return errors
