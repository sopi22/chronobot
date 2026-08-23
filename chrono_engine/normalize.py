"""Deterministic, script-agnostic normalization run before any pattern matching.

Two jobs, both mechanical (no interpretation of meaning):
1. Fold any Unicode decimal digit (full-width Latin, Devanagari, Arabic-Indic,
   etc.) to its ASCII equivalent.
2. Strip a small fixed whitelist of leading English prepositions ("at ",
   "by ", "around ", "@") that carry no temporal meaning of their own.
"""
from __future__ import annotations

import unicodedata

_LEADING_PREPOSITIONS = ("at ", "by ", "around ", "@")


def fold_unicode_digits(text: str) -> str:
    """Replace every Unicode decimal-digit character with its ASCII digit.

    Covers full-width Latin digits (e.g. U+FF11 '1'), Devanagari, Arabic-Indic,
    and any other script in Unicode category Nd -- not just NFKC-compatible
    full-width variants, which is why this iterates codepoints explicitly
    rather than relying on unicodedata.normalize('NFKC', ...) alone.
    """
    out_chars = []
    for ch in text:
        digit_value = unicodedata.digit(ch, None)
        if digit_value is not None:
            out_chars.append(str(digit_value))
        else:
            out_chars.append(ch)
    return "".join(out_chars)


def strip_leading_preposition(text: str) -> str:
    lowered = text.lower()
    for prep in _LEADING_PREPOSITIONS:
        if lowered.startswith(prep):
            return text[len(prep):]
    return text


def preprocess(raw: str) -> str:
    """Full normalization pass applied before any parsing pattern is tried."""
    text = raw.strip()
    text = fold_unicode_digits(text)
    text = strip_leading_preposition(text.strip())
    return text.strip()
