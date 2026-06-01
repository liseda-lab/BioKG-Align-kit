"""
Command-line interface for the BioKG-Align participant kit.

Subcommands:

* ``score`` — score predictions against the public answers/cands TSV
  for a split. Reports the diagnostic family always; the
  ``preferred_typed_*`` and ``hierarchy_aware_typed_*`` families are
  reported when the corresponding sibling TSVs are present.
* ``verify`` — validate a block-format submission TSV against the
  candidates TSV before upload to the platform.
* ``run-baseline`` — run a reference baseline (``random`` or
  ``hybrid_lexical``) end-to-end against a data directory.
* ``summarize-data`` — print a JSON summary of a data directory.
* ``build-graded-relevance`` — build a graded-relevance TSV from a
  preferred-pair file, a candidates file, and a hierarchy source.
"""

from __future__ import annotations

import argparse
import json
import sys

from . import __version__
from .baselines import predict, SUPPORTED_BASELINES
from .data import summarize_data
from .scoring import score_files
from .validation import validate_submission


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="biokg-align-kit")
    parser.add_argument(
        "--version",
        action="version",
        version=f"biokg-align-kit {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ----- score --------------------------------------------------------
    score_parser = subparsers.add_parser(
        "score",
        help="Score predictions against the public answers/cands TSV.",
    )
    score_parser.add_argument(
        "--predictions",
        required=True,
        help=(
            "Path to the participant submission TSV (4-column block "
            "format under v0.2.0)."
        ),
    )
    score_parser.add_argument(
        "--answers",
        required=True,
        help=(
            "Path to the public train/valid cands TSV (which carries "
            "the gold + QueryID + TgtCandidates columns) or, on the "
            "organiser side, the private test answers TSV. Provides "
            "both the gold pairs and the per-query candidate sets."
        ),
    )
    score_parser.add_argument("--output", help="Optional JSON output path.")
    score_parser.add_argument(
        "--preferred",
        default=None,
        help=(
            "Optional path to a *.preferred.tsv file for preferred-pair "
            "metrics (paper §1.5 primary). Defaults to the conventional "
            "sibling path next to the answers file."
        ),
    )
    score_parser.add_argument(
        "--graded",
        default=None,
        help=(
            "Optional path to a *.graded.tsv file for Hierarchy-Aware "
            "Typed nDCG@10 (paper §1.5 secondary). Defaults to the "
            "conventional sibling path."
        ),
    )
    score_parser.add_argument(
        "--candidate-count",
        type=int,
        default=None,
        help=(
            "Number of candidates per query in the block-format "
            "submission. When omitted (default), the scorer auto-"
            "detects from the first row of the answers TSV. Pass an "
            "explicit value to pin the expected count (50 is the "
            "canonical release shape)."
        ),
    )
    score_parser.add_argument(
        "--submission-format",
        choices=("block", "row"),
        default="block",
        help=(
            "Submission format. 'block' (default, v0.2.0 canonical) "
            "interprets the predictions file as a 4-column TSV whose "
            "rows align positionally with the answers TSV's rows in "
            "blocks of candidate_count * |relations|. 'row' expects a "
            "5-column TSV with explicit QueryID — only for round-"
            "tripping legacy fixtures."
        ),
    )

    # ----- verify ------------------------------------------------------
    validate_parser = subparsers.add_parser(
        "verify",
        help="Validate a block-format submission TSV against a candidates TSV.",
    )
    validate_parser.add_argument("--predictions", required=True)
    validate_parser.add_argument(
        "--candidates",
        required=True,
        help=(
            "Path to the candidates TSV for the relevant (task, split). "
            "The public test.cands.tsv has 2 columns "
            "(SrcEntity, TgtCandidates); the public train/valid "
            "cands.tsv carries gold columns in addition."
        ),
    )
    validate_parser.add_argument(
        "--candidates-per-query",
        type=int,
        default=None,
        help=(
            "Expected number of candidates per query. Defaults to the "
            "canonical value (50). Pass 0 to disable the check (e.g., "
            "for the bundled mini fixture, which has 4 candidates per "
            "query)."
        ),
    )

    # ----- run-baseline ------------------------------------------------
    baseline_parser = subparsers.add_parser(
        "run-baseline",
        help="Run a reference baseline (random / hybrid_lexical).",
    )
    baseline_parser.add_argument("--data-dir", required=True)
    baseline_parser.add_argument("--task", required=True)
    baseline_parser.add_argument(
        "--split", required=True, choices=["train", "valid", "test"]
    )
    baseline_parser.add_argument(
        "--baseline",
        required=True,
        choices=list(SUPPORTED_BASELINES),
        help="Baseline name. Choices: random, hybrid_lexical.",
    )
    baseline_parser.add_argument("--output", required=True)
    baseline_parser.add_argument("--seed", type=int, default=17)

    # ----- summarize-data ----------------------------------------------
    summary_parser = subparsers.add_parser(
        "summarize-data",
        help="Print a JSON summary of a data directory.",
    )
    summary_parser.add_argument("--data-dir", required=True)

    # ----- build-graded-relevance --------------------------------------
    graded_parser = subparsers.add_parser(
        "build-graded-relevance",
        help=(
            "Build a graded-relevance TSV for Hierarchy-Aware Typed "
            "nDCG@10 (paper §1.5) from a preferred-pair file + "
            "candidates + hierarchy."
        ),
    )
    graded_parser.add_argument(
        "--preferred",
        required=True,
        help="Path to *.preferred.tsv (SrcEntity, QueryID, TgtEntity, Relation).",
    )
    graded_parser.add_argument(
        "--candidates",
        required=True,
        help=(
            "Path to *.cands.tsv (with TgtCandidates and ideally "
            "QueryID); provides the candidate set per query."
        ),
    )
    graded_parser.add_argument(
        "--triples",
        default=None,
        help=(
            "Path to graph/triples.csv. Rows with relation 'subclass_of' "
            "are used to build the hierarchy index. Mutually exclusive "
            "with --hierarchy."
        ),
    )
    graded_parser.add_argument(
        "--hierarchy",
        default=None,
        help=(
            "Path to a pre-extracted hierarchy TSV with 'child_id' and "
            "'parent_id' columns. Mutually exclusive with --triples."
        ),
    )
    graded_parser.add_argument(
        "--output",
        required=True,
        help="Output path for the graded-relevance TSV.",
    )
    graded_parser.add_argument(
        "--max-distance",
        type=int,
        default=3,
        help="Maximum hierarchy distance for partial credit (default: 3).",
    )

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "score":
        metrics = score_files(
            args.predictions,
            args.answers,
            args.output,
            preferred_path=args.preferred,
            graded_path=args.graded,
            candidate_count=args.candidate_count,
            submission_format=args.submission_format,
        )
        for key, value in metrics.items():
            print(f"{key}\t{value:.6f}")
    elif args.command == "verify":
        if args.candidates_per_query is None:
            kwargs = {}
        elif args.candidates_per_query == 0:
            kwargs = {"candidates_per_query": None}
        else:
            kwargs = {"candidates_per_query": args.candidates_per_query}
        result = validate_submission(
            args.predictions, args.candidates, **kwargs
        )
        for warning in result.warnings:
            print(f"WARNING: {warning}", file=sys.stderr)
        if result.errors:
            for error in result.errors:
                print(f"ERROR: {error}", file=sys.stderr)
            raise SystemExit(1)
        if result.warnings:
            print(
                f"Submission validation passed with "
                f"{len(result.warnings)} warning(s); see stderr."
            )
        else:
            print("Submission validation passed")
    elif args.command == "run-baseline":
        output = predict(
            args.data_dir, args.task, args.split,
            args.baseline, args.output, args.seed,
        )
        print(f"Wrote predictions to {output}")
    elif args.command == "summarize-data":
        print(json.dumps(summarize_data(args.data_dir), indent=2, sort_keys=True))
    elif args.command == "build-graded-relevance":
        _run_build_graded_relevance(args)


def _run_build_graded_relevance(args: argparse.Namespace) -> None:
    """Build per-query graded relevance from preferred pairs + candidates
    + hierarchy, and write the TSV."""
    from pathlib import Path

    from .hierarchy import (
        HierarchyIndex,
        compute_graded_relevance,
        load_hierarchy_from_triples,
        write_graded_relevance,
    )
    from .io import parse_list, read_tsv
    from .scoring import load_preferred_pairs

    if (args.triples is None) == (args.hierarchy is None):
        raise SystemExit(
            "build-graded-relevance: exactly one of --triples or "
            "--hierarchy must be given."
        )

    if args.triples is not None:
        hierarchy = load_hierarchy_from_triples(args.triples)
    else:
        edges = read_tsv(args.hierarchy)
        hierarchy = HierarchyIndex(edges)

    preferred = load_preferred_pairs(args.preferred)
    candidates_by_query: dict[tuple[str, str], set[str]] = {}
    for row in read_tsv(args.candidates):
        key = (row["SrcEntity"], row.get("QueryID", "Q0"))
        candidates_by_query[key] = set(parse_list(row.get("TgtCandidates", "[]")))

    per_query_gains: dict[tuple[str, str], dict[tuple[str, str], float]] = {}
    skipped_no_candidates = 0
    for query_key, (tgt, rel) in preferred.items():
        if query_key not in candidates_by_query:
            skipped_no_candidates += 1
            continue
        gains = compute_graded_relevance(
            preferred_target=tgt,
            preferred_relation=rel,
            candidate_set=candidates_by_query[query_key],
            hierarchy=hierarchy,
            max_distance=args.max_distance,
        )
        if gains:
            per_query_gains[query_key] = gains

    skipped_no_preferred = sum(
        1 for key in candidates_by_query if key not in preferred
    )

    write_graded_relevance(args.output, per_query_gains)
    print(
        f"Wrote graded relevance for {len(per_query_gains)} query/queries "
        f"to {args.output}"
    )
    if skipped_no_preferred:
        print(
            f"  skipped {skipped_no_preferred} candidate query/queries with "
            f"no preferred-pair entry (these don't contribute to the "
            f"hierarchy-aware metric either)"
        )
    if skipped_no_candidates:
        print(
            f"  skipped {skipped_no_candidates} preferred-pair entry/entries "
            f"that weren't found in the candidates file"
        )
