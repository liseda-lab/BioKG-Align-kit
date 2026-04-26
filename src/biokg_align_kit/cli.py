from __future__ import annotations

import argparse
import json
import sys

from .baselines import predict
from .data import summarize_data
from .scoring import score_files
from .validation import validate_submission


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="biokg-align-kit")
    subparsers = parser.add_subparsers(dest="command", required=True)

    score_parser = subparsers.add_parser("score", help="Score predictions against an answer TSV.")
    score_parser.add_argument("--predictions", required=True)
    score_parser.add_argument("--answers", required=True)
    score_parser.add_argument("--output")

    validate_parser = subparsers.add_parser("validate-submission", help="Validate a prediction TSV against a candidate file.")
    validate_parser.add_argument("--predictions", required=True)
    validate_parser.add_argument("--candidates", required=True)

    baseline_parser = subparsers.add_parser("run-baseline", help="Run a simple public baseline.")
    baseline_parser.add_argument("--data-dir", required=True)
    baseline_parser.add_argument("--task", required=True)
    baseline_parser.add_argument("--split", required=True, choices=["train", "valid", "test"])
    baseline_parser.add_argument("--baseline", required=True, choices=["random", "lexical"])
    baseline_parser.add_argument("--output", required=True)
    baseline_parser.add_argument("--seed", type=int, default=17)

    summary_parser = subparsers.add_parser("summarize-data", help="Summarize a BioKG-Align data directory.")
    summary_parser.add_argument("--data-dir", required=True)

    args = parser.parse_args(argv)
    if args.command == "score":
        metrics = score_files(args.predictions, args.answers, args.output)
        for key, value in metrics.items():
            print(f"{key}\t{value:.6f}")
    elif args.command == "validate-submission":
        errors = validate_submission(args.predictions, args.candidates)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            raise SystemExit(1)
        print("Submission validation passed")
    elif args.command == "run-baseline":
        output = predict(args.data_dir, args.task, args.split, args.baseline, args.output, args.seed)
        print(f"Wrote predictions to {output}")
    elif args.command == "summarize-data":
        print(json.dumps(summarize_data(args.data_dir), indent=2, sort_keys=True))
