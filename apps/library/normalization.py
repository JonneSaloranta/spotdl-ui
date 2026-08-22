"""Metadata normalization for probable-duplicate matching (CLAUDE.md #7)."""

import re
import unicodedata

_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(value: str) -> str:
    """Fold `value` to a comparable form: NFKD-normalized, accents
    stripped, lowercased, punctuation removed, whitespace collapsed.

    Used only for *matching* (duplicate detection), never for display or
    storage of the original metadata.
    """
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = _PUNCTUATION_RE.sub(" ", value)
    value = _WHITESPACE_RE.sub(" ", value).strip()
    return value
