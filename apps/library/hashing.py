"""SHA-256 file hashing (CLAUDE.md #7). The canonical hash used everywhere
for exact-duplicate detection."""

from __future__ import annotations

import hashlib
from pathlib import Path

_CHUNK_SIZE = 1024 * 1024  # 1 MiB


def sha256_file(path: Path | str) -> str:
    """Return the lowercase hex SHA-256 digest of the file at `path`.

    Streams the file in chunks so large audio files do not need to be
    loaded into memory at once.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
