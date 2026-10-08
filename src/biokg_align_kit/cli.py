"""
Command-line interface for the BioKG-Align participant kit.

Subcommands:

* ``score`` — score a five-column submission against the public train/valid
  evaluation files of a release. **Lenient by default** (violations become
  warnings with a documented fallback); ``--strict`` applies the platform rules.
* ``verify`` — validate a submission against the public candidate files with the
  platform's strict rules before upload.
* ``run-baseline`` — run a reference baseline (``random`` or ``hybrid_lexical``).
* ``summarize-data`` — print a JSON summary of a data directory.
* ``build-graded-relevance`` — build a graded-relevance TSV from a preferred-pair
  file, a candidates file and a hierarchy source.
"""

from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

from . import __version__
from .baselines import SUPPORTED_BASELINES, predict
from .data import summarize_data
from .scoring import (
    EvaluationSetError,
    SubmissionError,
    load_query_index,
    load_submission,
    score_submission,
    score_task,
    submission_result,
    task_names,
)
from .validation import validate_submission


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="biokg-align-kit")
    parser.add_argument("--version", action="version", version=f"biokg-align-kit {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ----- score --------------------------------------------------------
    score_parser = subparsers.add_parser(
        "score",
        help="Score a submission against a release's public train/valid evaluation files.",
    )
    score_parser.add_argument("--predictions", required=True, help="Five-column submission TSV.")
    source = score_parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--data-dir",
        help="Release directory (tasks/ and evaluation/). Scores every task with a <split>.cands.tsv unless --tasks is given.",
    )
    source.add_argument(
        "--answers",
        help=(
            "Score a single task against one answers file "
            "(<eval>/<task>/<split>.answers.tsv; its preferred/graded siblings and "
            "<eval>/query_metadata.tsv are used)."
        ),
    )
    score_parser.add_argument("--split", choices=("train", "valid"), help="Split to score (required with --data-dir).")
    score_parser.add_argument("--tasks", nargs="+", default=None, help="Restrict scoring to these tasks.")
    score_parser.add_argument(
        "--evaluation-dir",
        default=None,
        help="Organisers: evaluation directory to score against (default <data-dir>/evaluation).",
    )
    score_parser.add_argument("--output", help="Optional JSON output path.")
    score_parser.add_argument(
        "--strict",
        action="store_true",
        help="Apply the platform rules: every format violation is fatal (default: lenient, with warnings).",
    )
    score_parser.add_argument("--graph-dir", default=None, help="Optional released graph directory for OWL 2 RL conflict scoring.")
    score_parser.add_argument("--souffle-bin", default="souffle", help="Souffle executable or path (default: souffle).")
    score_parser.add_argument("--conflict-report", default=None, help="Optional decoded Datalog conflict report JSON path.")

    # ----- verify ------------------------------------------------------
    verify_parser = subparsers.add_parser(
        "verify",
        help="Validate a submission against the public candidate files (platform rules).",
    )
    verify_parser.add_argument("--predictions", required=True)
    verify_parser.add_argument("--data-dir", required=True, help="Release directory containing tasks/.")
    verify_parser.add_argument("--split", default="test", choices=("train", "valid", "test"))
    verify_parser.add_argument("--tasks", nargs="+", default=None, help="Tasks the submission covers (default: all).")

    # ----- run-baseline ------------------------------------------------
    baseline_parser = subparsers.add_parser("run-baseline", help="Run a reference baseline (random / hybrid_lexical).")
    baseline_parser.add_argument("--data-dir", required=True)
    baseline_parser.add_argument("--task", required=True)
    baseline_parser.add_argument("--split", required=True, choices=["train", "valid", "test"])
    baseline_parser.add_argument(
        "--baseline", required=True, choices=list(SUPPORTED_BASELINES), help="Baseline name. Choices: random, hybrid_lexical."
    )
    baseline_parser.add_argument("--output", required=True)
    baseline_parser.add_argument("--seed", type=int, default=17)

    # ----- summarize-data ----------------------------------------------
    summary_parser = subparsers.add_parser("summarize-data", help="Print a JSON summary of a data directory.")
    summary_parser.add_argument("--data-dir", required=True)

    # ----- build-graded-relevance --------------------------------------
    graded_parser = subparsers.add_parser(
        "build-graded-relevance",
        help="Build a graded-relevance TSV for Hierarchy-Aware Typed nDCG@10 from preferred pairs + candidates + hierarchy.",
    )
    graded_parser.add_argument("--preferred", required=True, help="Path to <split>.preferred.tsv (QueryID, SrcEntity, TgtEntity, Relation).")
    graded_parser.add_argument("--candidates", required=True, help="Path to the cands or answers TSV providing each query's pool.")
    graded_parser.add_argument("--triples", default=None, help="graph/triples.csv ('subclass_of' rows). Mutually exclusive with --hierarchy.")
    graded_parser.add_argument("--hierarchy", default=None, help="Hierarchy TSV with child_id, parent_id. Mutually exclusive with --triples.")
    graded_parser.add_argument("--output", required=True, help="Output path for the graded-relevance TSV.")
    graded_parser.add_argument("--max-distance", type=int, default=3, help="Maximum hierarchy distance for partial credit (default: 3).")
    graded_parser.add_argument(
        "--equivalence-partial-gain",
        type=float,
        default=0.6,
        help="Gain of the same-entity subsumption directions for an equivalence-preferred query, scaled by 1/(d+1) along the hierarchy (default: 0.6).",
    )
    return parser


def _print_metrics(result: dict) -> None:
    for task, metrics in result.items():
        if not isinstance(metrics, dict):
            continue
        for key, value in sorted(metrics.items()):
            print(f"{task}\t{key}\t{value:.6f}")


def _run_score(args: argparse.Namespace) -> None:
    strict = bool(args.strict)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            if args.answers:
                answers = Path(args.answers)
                task = answers.parent.name
                split = answers.name.removesuffix(".answers.tsv")
                if split not in {"train", "valid"}:
                    raise SystemExit(f"--answers must be a train or valid answers file, got {answers.name}")
                index = load_query_index([answers])
                loaded = load_submission(args.predictions, index, strict=strict)
                per_task = {task: score_task(loaded, answers.parent.parent, task, split, strict=strict)}
                result: dict = submission_result(per_task, loaded)
            else:
                if not args.split:
                    raise SystemExit("--split is required with --data-dir")
                result = score_submission(
                    args.predictions, args.data_dir, args.evaluation_dir, args.split, tasks=args.tasks, strict=strict
                )
                if args.graph_dir:
                    from .conflict import score_datalog_conflicts

                    data_dir = Path(args.data_dir)
                    tasks = args.tasks or task_names(data_dir / "tasks", f"{args.split}.cands.tsv")
                    index = load_query_index([data_dir / "tasks" / task / f"{args.split}.cands.tsv" for task in tasks])
                    loaded = load_submission(args.predictions, index, strict=strict)
                    result["datalog_conflicts"] = score_datalog_conflicts(
                        loaded.predictions_by_query,
                        args.graph_dir,
                        souffle_bin=args.souffle_bin,
                        report_path=args.conflict_report,
                    )
        except (SubmissionError, EvaluationSetError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            raise SystemExit(1) from None
    user_warnings = [warning for warning in caught if issubclass(warning.category, UserWarning)]
    for warning in user_warnings:
        print(f"WARNING: {warning.message}", file=sys.stderr)
    if user_warnings:
        print(
            "NOTE: lenient scoring applied fallbacks; these numbers are NOT leaderboard-comparable. "
            "Use --strict (or `verify`) to see the platform's verdict.",
            file=sys.stderr,
        )
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _print_metrics(result)


def _run_verify(args: argparse.Namespace) -> None:
    tasks_root = Path(args.data_dir) / "tasks"
    filename = f"{args.split}.cands.tsv"
    tasks = args.tasks or task_names(tasks_root, filename)
    result = validate_submission(args.predictions, [tasks_root / task / filename for task in tasks])
    if result.errors:
        for error in result.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Submission validation passed ({len(tasks)} task(s), split {args.split})")


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "score":
        _run_score(args)
    elif args.command == "verify":
        _run_verify(args)
    elif args.command == "run-baseline":
        output = predict(args.data_dir, args.task, args.split, args.baseline, args.output, args.seed)
        print(f"Wrote predictions to {output}")
    elif args.command == "summarize-data":
        print(json.dumps(summarize_data(args.data_dir), indent=2, sort_keys=True))
    elif args.command == "build-graded-relevance":
        _run_build_graded_relevance(args)


def _run_build_graded_relevance(args: argparse.Namespace) -> None:
    """Build per-query graded relevance from preferred pairs + candidates +
    hierarchy, and write the TSV."""
    from .hierarchy import HierarchyIndex, compute_graded_relevance, load_hierarchy_from_triples, write_graded_relevance
    from .io import parse_list, read_tsv
    from .scoring import load_preferred_pairs

    if (args.triples is None) == (args.hierarchy is None):
        raise SystemExit("build-graded-relevance: exactly one of --triples or --hierarchy must be given.")
    hierarchy = load_hierarchy_from_triples(args.triples) if args.triples is not None else HierarchyIndex(read_tsv(args.hierarchy))

    preferred = load_preferred_pairs(args.preferred)
    pools: dict[str, set[str]] = {}
    sources: dict[str, str] = {}
    for row in read_tsv(args.candidates):
        pools[row["QueryID"]] = set(parse_list(row.get("TgtCandidates", "[]")))
        sources[row["QueryID"]] = row["SrcEntity"]

    per_query_gains: dict[str, dict[tuple[str, str], float]] = {}
    skipped_no_candidates = 0
    for query_id, (target, relation) in preferred.items():
        if query_id not in pools:
            skipped_no_candidates += 1
            continue
        gains = compute_graded_relevance(
            preferred_target=target,
            preferred_relation=relation,
            candidate_set=pools[query_id],
            hierarchy=hierarchy,
            max_distance=args.max_distance,
            equivalence_partial_gain=args.equivalence_partial_gain,
        )
        if gains:
            per_query_gains[query_id] = gains
    skipped_no_preferred = sum(1 for query_id in pools if query_id not in preferred)
    write_graded_relevance(args.output, per_query_gains, sources)
    print(f"Wrote graded relevance for {len(per_query_gains)} query/queries to {args.output}")
    if skipped_no_preferred:
        print(f"  skipped {skipped_no_preferred} candidate query/queries with no preferred-pair entry")
    if skipped_no_candidates:
        print(f"  skipped {skipped_no_candidates} preferred-pair entry/entries not found in the candidates file")
