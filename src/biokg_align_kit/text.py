from __future__ import annotations

import re
from collections import Counter

_PUNCT = re.compile(r"[^a-z0-9]+")


def normalize_text(value: str) -> str:
    normalized = _PUNCT.sub(" ", value.lower()).strip()
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
    exact = 1.0 if normalize_text(left) == normalize_text(right) else 0.0
    left_tokens = set(tokenize(left))
    right_tokens = set(tokenize(right))
    jaccard = len(left_tokens & right_tokens) / len(left_tokens | right_tokens) if left_tokens and right_tokens else 0.0
    char_score = cosine_counter(char_ngrams(left), char_ngrams(right))
    return max(exact, 0.65 * jaccard + 0.35 * char_score)
