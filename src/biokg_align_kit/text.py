from __future__ import annotations

import re
import unicodedata
from collections import Counter

_PUNCT = re.compile(r"[^a-z0-9]+")

# Kept identical to the organiser implementation: Greek letters are
# meaning-bearing in biomedical labels (IFN-α vs IFN-γ), so they transliterate
# to their names instead of vanishing as punctuation; NFKD then strips
# combining accents so e.g. 'é' survives as 'e'.
_GREEK_TRANSLITERATION = {
    "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon",
    "ζ": "zeta", "η": "eta", "θ": "theta", "ι": "iota", "κ": "kappa",
    "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "ο": "omicron",
    "π": "pi", "ρ": "rho", "σ": "sigma", "ς": "sigma", "τ": "tau",
    "υ": "upsilon", "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
}
_GREEK_PATTERN = re.compile("|".join(_GREEK_TRANSLITERATION))


def _transliterate(value: str) -> str:
    lowered = value.lower()
    if _GREEK_PATTERN.search(lowered):
        lowered = _GREEK_PATTERN.sub(
            lambda match: f" {_GREEK_TRANSLITERATION[match.group(0)]} ", lowered
        )
    decomposed = unicodedata.normalize("NFKD", lowered)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def normalize_text(value: str) -> str:
    normalized = _PUNCT.sub(" ", _transliterate(value)).strip()
    return " ".join(normalize_plural(token) for token in normalized.split() if token)


def normalize_plural(token: str) -> str:
    if len(token) > 4 and token.endswith("ies"):
        return token[:-3] + "y"
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def tokenize(value: str) -> list[str]:
    return normalize_text(value).split()


def char_ngrams(value: str, n: int = 3) -> Counter[str]:
    compact = normalize_text(value).replace(" ", "")
    if len(compact) < n:
        return Counter([compact]) if compact else Counter()
    return Counter(compact[index : index + n] for index in range(len(compact) - n + 1))


def cosine_counter(left: Counter[str], right: Counter[str]) -> float:
    if not left or not right:
        return 0.0
    numerator = sum(left[key] * right.get(key, 0) for key in left)
    left_norm = sum(value * value for value in left.values()) ** 0.5
    right_norm = sum(value * value for value in right.values()) ** 0.5
    return numerator / (left_norm * right_norm) if left_norm and right_norm else 0.0


def lexical_score(left: str, right: str) -> float:
    left_norm = normalize_text(left)
    right_norm = normalize_text(right)
    # guard against both being empty: an entity with no label/synonyms
    # must not score 1.0 against any other unlabelled entity
    exact = 1.0 if left_norm and left_norm == right_norm else 0.0
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens) if left_tokens and right_tokens else 0.0
    char_score = cosine_counter(char_ngrams(left), char_ngrams(right))
    return max(exact, 0.65 * jaccard + 0.35 * char_score)
