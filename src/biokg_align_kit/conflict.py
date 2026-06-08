from __future__ import annotations

# This module intentionally shares the organizer implementation. Keeping the
# evaluator self-contained makes public-kit scores reproducible without the
# organizer package.

import csv
import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Iterable


OWL_EQUIVALENT_CLASS = "http://www.w3.org/2002/07/owl#equivalentClass"
RDFS_SUBCLASS_OF = "http://www.w3.org/2000/01/rdf-schema#subClassOf"
CONFLICT_METRIC_VERSION = "owl2rl-conflict-v1"
RELATION_ORDER = {
    "equivalent": 0,
    "source_subsumed_by_target": 1,
    "source_subsumes_target": 2,
}
Witness = tuple[str, str, str, str, str]
Mapping = tuple[str, str, str]

_BASELINE_CACHE: dict[tuple[str, tuple[tuple[str, int, int], ...], str], set[Witness]] = {}


def _rank_key(row: dict[str, str]) -> tuple[float, str, int]:
    return (
        -float(row.get("Score", 0.0)),
        row["TgtEntity"],
        RELATION_ORDER.get(row["Relation"], len(RELATION_ORDER)),
    )


def select_top_mappings(
    predictions: list[dict[str, str]],
    query_keys: Iterable[tuple[str, str]] | None = None,
    per_query_candidate_sets: dict[tuple[str, str], set[str]] | None = None,
) -> list[Mapping]:
    """Select one deterministic top-ranked mapping per query."""
    explicit_query_ids = any("QueryID" in row for row in predictions)
    by_query: dict[tuple[str, str], list[dict[str, str]]] = {}

    if explicit_query_ids:
        for row in predictions:
            key = (row["SrcEntity"], row.get("QueryID", "Q0"))
            by_query.setdefault(key, []).append(row)
        keys = sorted(set(query_keys) if query_keys is not None else by_query)
    elif query_keys is not None:
        keys = sorted(set(query_keys))
        by_source: dict[str, list[dict[str, str]]] = {}
        for row in predictions:
            by_source.setdefault(row["SrcEntity"], []).append(row)
        for key in keys:
            source, _query_id = key
            candidates = per_query_candidate_sets.get(key) if per_query_candidate_sets else None
            rows = by_source.get(source, [])
            by_query[key] = [
                row for row in rows
                if candidates is None or row["TgtEntity"] in candidates
            ]
    else:
        for row in predictions:
            by_query.setdefault((row["SrcEntity"], "Q0"), []).append(row)
        keys = sorted(by_query)

    selected: set[Mapping] = set()
    for key in keys:
        ranked = sorted(by_query.get(key, []), key=_rank_key)
        if ranked:
            row = ranked[0]
            selected.add((row["SrcEntity"], row["TgtEntity"], row["Relation"]))
    return sorted(selected)


def mapping_to_rdf(mapping: Mapping) -> tuple[str, str, str]:
    source, target, relation = mapping
    if relation == "equivalent":
        return source, OWL_EQUIVALENT_CLASS, target
    if relation == "source_subsumed_by_target":
        return source, RDFS_SUBCLASS_OF, target
    if relation == "source_subsumes_target":
        return target, RDFS_SUBCLASS_OF, source
    raise ValueError(f"Unsupported mapping relation for Datalog conflict scoring: {relation!r}")


def score_datalog_conflicts(
    predictions: list[dict[str, str]],
    graph_dir: str | Path,
    *,
    query_keys: Iterable[tuple[str, str]] | None = None,
    per_query_candidate_sets: dict[tuple[str, str], set[str]] | None = None,
    souffle_bin: str = "souffle",
    report_path: str | Path | None = None,
) -> dict[str, float]:
    graph_dir = Path(graph_dir)
    _validate_graph(graph_dir)
    mappings = select_top_mappings(predictions, query_keys, per_query_candidate_sets)
    facts, resolved_mappings, terms = _mapping_facts(graph_dir, mappings)

    cache_key = _baseline_cache_key(graph_dir, souffle_bin)
    baseline = _BASELINE_CACHE.get(cache_key)
    if baseline is None:
        baseline = _execute_conflict_program(graph_dir, [], souffle_bin)
        _BASELINE_CACHE[cache_key] = baseline
    total = _execute_conflict_program(graph_dir, facts, souffle_bin)
    induced = total - baseline
    mapping_count = len(facts)
    metrics = {
        "datalog_inconsistency_count": float(len(induced)),
        "datalog_mapping_count": float(mapping_count),
        "datalog_inconsistencies_per_mapping": (
            float(len(induced)) / mapping_count if mapping_count else 0.0
        ),
        "datalog_inconsistency_rule_count": float(len({row[0] for row in induced})),
        "datalog_conflict_free": 1.0 if not induced else 0.0,
        "datalog_baseline_inconsistency_count": float(len(baseline)),
        "datalog_total_inconsistency_count": float(len(total)),
    }
    if report_path is not None:
        _write_report(
            Path(report_path), metrics, resolved_mappings,
            baseline, total, induced, terms,
        )
    return metrics


def _validate_graph(graph_dir: Path) -> None:
    required = [
        "facts.dl", "owl2rl_core.dl", "conflict_rules.dl",
        "datalog_terms.tsv", "datalog_schema.json", "properties.csv",
    ]
    missing = [name for name in required if not (graph_dir / name).is_file()]
    if missing:
        raise ValueError(
            f"Datalog conflict scoring requires graph files {missing} under {graph_dir}"
        )
    schema = json.loads((graph_dir / "datalog_schema.json").read_text(encoding="utf-8"))
    if schema.get("conflict_metric_version") != CONFLICT_METRIC_VERSION:
        raise ValueError(
            "Unsupported Datalog conflict metric version: "
            f"{schema.get('conflict_metric_version')!r}; expected {CONFLICT_METRIC_VERSION!r}"
        )


def _mapping_facts(
    graph_dir: Path,
    mappings: list[Mapping],
) -> tuple[list[str], list[dict[str, str]], dict[str, dict[str, str]]]:
    with (graph_dir / "properties.csv").open("r", encoding="utf-8", newline="") as handle:
        node_iris = {
            row["node_id"]: row["iri"] for row in csv.DictReader(handle)
            if row.get("node_id") and row.get("iri")
        }
    with (graph_dir / "datalog_terms.tsv").open("r", encoding="utf-8", newline="") as handle:
        term_rows = list(csv.DictReader(handle, delimiter="\t"))
    terms = {row["term_id"]: row for row in term_rows}
    iri_terms = {
        row["lexical"]: row["term_id"] for row in term_rows
        if row.get("term_type") == "iri" and row.get("lexical")
    }

    resolved_by_fact: dict[str, dict[str, str]] = {}
    for mapping in mappings:
        subject_node, predicate_iri, object_node = mapping_to_rdf(mapping)
        try:
            subject_iri = node_iris[subject_node]
            object_iri = node_iris[object_node]
        except KeyError as exc:
            raise ValueError(
                f"Selected mapping entity {exc.args[0]!r} is absent from properties.csv"
            ) from exc
        missing_iris = [
            iri for iri in (subject_iri, predicate_iri, object_iri)
            if iri not in iri_terms
        ]
        if missing_iris:
            raise ValueError(
                "Selected mapping cannot be represented by datalog_terms.tsv; "
                f"missing IRI term(s): {missing_iris}"
            )
        fact = (
            f'source_triple("submission", {_quote(iri_terms[subject_iri])}, '
            f'{_quote(iri_terms[predicate_iri])}, {_quote(iri_terms[object_iri])}).'
        )
        resolved_by_fact.setdefault(fact, {
            "source_entity": mapping[0],
            "target_entity": mapping[1],
            "relation": mapping[2],
            "subject_iri": subject_iri,
            "predicate_iri": predicate_iri,
            "object_iri": object_iri,
        })
    facts = sorted(resolved_by_fact)
    resolved = [resolved_by_fact[fact] for fact in facts]
    return facts, resolved, terms


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _baseline_cache_key(
    graph_dir: Path,
    souffle_bin: str,
) -> tuple[str, tuple[tuple[str, int, int], ...], str]:
    names = ("facts.dl", "owl2rl_core.dl", "conflict_rules.dl")
    stats = tuple(
        (name, (graph_dir / name).stat().st_size, (graph_dir / name).stat().st_mtime_ns)
        for name in names
    )
    return str(graph_dir.resolve()), stats, souffle_bin


def _execute_conflict_program(
    graph_dir: Path,
    additional_facts: list[str],
    souffle_bin: str,
) -> set[Witness]:
    executable = shutil.which(souffle_bin)
    if executable is None and Path(souffle_bin).is_file():
        executable = str(Path(souffle_bin).resolve())
    if executable is None:
        raise RuntimeError(
            f"Souffle executable {souffle_bin!r} was not found; "
            "install Souffle or omit --graph-dir to skip Datalog conflict scoring"
        )
    with tempfile.TemporaryDirectory(prefix="biokg-align-conflict-") as tmp:
        work = Path(tmp)
        for name in ("owl2rl_core.dl", "conflict_rules.dl"):
            shutil.copyfile(graph_dir / name, work / name)
        facts = (graph_dir / "facts.dl").read_text(encoding="utf-8")
        if additional_facts:
            facts = facts.rstrip() + "\n" + "\n".join(additional_facts) + "\n"
        (work / "facts.dl").write_text(facts, encoding="utf-8")
        output_dir = work / "output"
        output_dir.mkdir()
        result = subprocess.run(
            [executable, "-D", str(output_dir), "conflict_rules.dl"],
            cwd=work, text=True, capture_output=True, check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise RuntimeError(f"Souffle conflict evaluation failed: {detail}")
        output = output_dir / "inconsistency.csv"
        return _read_witnesses(output) if output.exists() else set()


def _read_witnesses(path: Path) -> set[Witness]:
    rows: set[Witness] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        values = next(csv.reader([line], delimiter="\t"))
        if len(values) == 1 and "," in line:
            values = next(csv.reader([line]))
        if len(values) != 5:
            raise ValueError(f"Malformed Souffle inconsistency row in {path}: {line!r}")
        rows.add(tuple(values))  # type: ignore[arg-type]
    return rows


def _write_report(
    path: Path,
    metrics: dict[str, float],
    mappings: list[dict[str, str]],
    baseline: set[Witness],
    total: set[Witness],
    induced: set[Witness],
    terms: dict[str, dict[str, str]],
) -> None:
    def witness(row: Witness) -> dict[str, object]:
        return {
            "rule_id": row[0],
            "arguments": [
                {"term_id": value, **terms.get(value, {"lexical": value})}
                for value in row[1:]
            ],
        }

    payload = {
        "metric_version": CONFLICT_METRIC_VERSION,
        "metrics": metrics,
        "selected_mappings": mappings,
        "baseline_witnesses": [witness(row) for row in sorted(baseline)],
        "total_witnesses": [witness(row) for row in sorted(total)],
        "induced_witnesses": [witness(row) for row in sorted(induced)],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
