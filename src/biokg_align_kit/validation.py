"""
Submission validator for BioKG-Align (``biokg-align-kit verify``).

Runs the strict submission loader (:func:`biokg_align_kit.scoring.load_submission`
with ``strict=True``) against a query index built from the public candidate files,
so a file that ``verify`` accepts is a file the platform accepts. Every violation is
an error; there are no warnings and no silent filters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .scoring import (
    DEFAULT_RELATIONS,
    EvaluationSetError,
    SubmissionError,
    load_query_index,
    load_submission,
)

RELATIONS: tuple[str, ...] = DEFAULT_RELATIONS


@dataclass
class ValidationResult:
    """Outcome of a submission validation pass. ``errors`` would make the platform
    reject the submission; ``warnings`` is kept for API stability and is always
    empty (every rule is fatal on the platform)."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.errors)

    def __iter__(self):
        return iter(self.errors)

    def __len__(self) -> int:
        return len(self.errors)


def validate_submission(
    predictions_path: str | Path,
    cands_paths: Iterable[str | Path],
    relations: tuple[str, ...] | list[str] = RELATIONS,
) -> ValidationResult:
    """Validate a five-column submission against the candidate files it answers
    (one ``tasks/<task>/<split>.cands.tsv`` per task covered)."""
    result = ValidationResult()
    try:
        index = load_query_index(cands_paths)
    except EvaluationSetError as exc:
        result.errors.extend(["candidate files are invalid:"] + exc.messages)
        return result
    try:
        load_submission(predictions_path, index, relations=relations, strict=True)
    except SubmissionError as exc:
        result.errors.extend(exc.messages)
    return result
