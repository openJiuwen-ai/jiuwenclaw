# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Deterministic text utilities with no model or tokenizer dependency."""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable

_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]")
_NUMBER_RE = re.compile(r"(?<![A-Za-z0-9_])[-+]?\d+(?:\.\d+)?%?")


def lexical_tokens(text: str) -> list[str]:
    """Tokenize English words/numbers and CJK characters deterministically."""

    return [token.lower() for token in _TOKEN_RE.findall(str(text or ""))]


def estimate_tokens(text: str) -> int:
    """Estimate prompt tokens consistently for selection and benchmarking.

    This is a transparent proxy, not a provider bill.  It counts alphanumeric
    groups and individual CJK characters, then applies a small punctuation
    allowance.  Real provider usage remains recorded separately by the ledger.
    """

    raw = str(text or "")
    lexical = len(lexical_tokens(raw))
    punctuation = len(re.findall(r"[^\w\s\u3400-\u9fff]", raw))
    return max(1, lexical + math.ceil(punctuation / 4)) if raw else 0


def cosine_similarity(left: str, right: str) -> float:
    """Return bag-of-words cosine similarity in ``[0, 1]``."""

    left_counts = Counter(lexical_tokens(left))
    right_counts = Counter(lexical_tokens(right))
    if not left_counts or not right_counts:
        return 0.0
    dot = sum(value * right_counts.get(token, 0) for token, value in left_counts.items())
    left_norm = math.sqrt(sum(value * value for value in left_counts.values()))
    right_norm = math.sqrt(sum(value * value for value in right_counts.values()))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def idf_overlap(query: str, document: str, corpus: Iterable[str]) -> float:
    """Compute normalized IDF-weighted query overlap."""

    query_terms = set(lexical_tokens(query))
    document_terms = set(lexical_tokens(document))
    if not query_terms or not document_terms:
        return 0.0
    documents = [set(lexical_tokens(item)) for item in corpus]
    denominator = 0.0
    numerator = 0.0
    for term in query_terms:
        frequency = sum(1 for terms in documents if term in terms)
        weight = math.log((len(documents) + 1) / (frequency + 1)) + 1.0
        denominator += weight
        if term in document_terms:
            numerator += weight
    return numerator / denominator if denominator else 0.0


def extract_numbers(text: str) -> list[str]:
    """Extract normalized numeric literals used by claim verification."""

    return [match.group(0).lstrip("+") for match in _NUMBER_RE.finditer(str(text or ""))]
