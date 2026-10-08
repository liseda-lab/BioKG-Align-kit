"""
Preferred-pair construction rule of the BioKG-Align contract.

Every query carries exactly one preferred (target, relation) gold pair; the
primary metric (Macro Preferred Typed MRR) ranks that pair. The organiser
pipeline builds the released ``preferred.tsv`` files with this function, so the
rule a participant reads here is the rule the release was built with.
"""

from __future__ import annotations

# Precedence when a query's gold set holds several relations.
PREFERRED_RELATION_PRECEDENCE: tuple[str, ...] = (
    "equivalent",
    "source_subsumed_by_target",
    "source_subsumes_target",
)


def select_preferred_pairs(gold_pairs: set[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    Select the preferred (target, relation) gold pairs from a query's gold
    set using the precedence policy. Returns the first non-empty (with precedence) from:
        [ equivalent, source_subsumed_by_target, source_subsumes_target ]
    as list[tuple[str, str]]; the preferred (target, relation) pairs.
    """
    if not gold_pairs:
        raise ValueError("select_preferred_pairs: gold set is empty; each query must have min(1) gold pair.")

    equivalent_pairs = sorted(
        (tgt, rel) for (tgt, rel) in gold_pairs if rel == "equivalent"
    )
    if equivalent_pairs:
        return equivalent_pairs

    ssbt_pairs = sorted(
        (tgt, rel) for (tgt, rel) in gold_pairs
        if rel == "source_subsumed_by_target"
    )
    if ssbt_pairs:
        return ssbt_pairs

    sst_pairs = sorted(
        (tgt, rel) for (tgt, rel) in gold_pairs
        if rel == "source_subsumes_target"
    )
    if sst_pairs:
        return sst_pairs

    raise ValueError("select_preferred_pairs: gold set contains no recognised relation.")


def query_mode(preferred_relation: str) -> str:
    """``QueryMode`` of a query: ``equivalence`` iff its preferred relation is
    ``equivalent``, else ``subsumption``."""
    if preferred_relation not in PREFERRED_RELATION_PRECEDENCE:
        raise ValueError(f"Unknown preferred relation: {preferred_relation!r}")
    return "equivalence" if preferred_relation == "equivalent" else "subsumption"
