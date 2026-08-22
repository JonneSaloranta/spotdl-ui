"""Filesystem-safe naming for finalized track files (CLAUDE.md #22).

spotDL renders its own `--output` template from track metadata, but once a
file is downloaded we still read metadata ourselves (e.g. after a
MusicBrainz update) and move/rename files as part of finalization. Any path
component built from metadata — which is attacker-influenced, since it
ultimately comes from a public catalog a user pointed us at — must be
sanitized before it touches the filesystem.
"""

from __future__ import annotations

import re
import unicodedata

# Windows reserved device names; harmless on Linux but cheap to avoid so
# library exports/backups stay portable.
_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

_CONTROL_CHARS = "".join(chr(c) for c in range(0x00, 0x20)) + chr(0x7F)
_UNSAFE_CHARS = re.compile(r'[<>:"/\\|?*' + re.escape(_CONTROL_CHARS) + r"]")
_MAX_COMPONENT_LENGTH = 150  # bytes-ish budget per path segment, well under ext4's 255


def sanitize_path_component(value: str, *, fallback: str = "unknown") -> str:
    """Sanitize a single path segment (a directory or file name, no separators).

    - normalizes unicode;
    - strips control characters and characters unsafe on common filesystems;
    - collapses `.` / `..` segments so they cannot escape the target directory;
    - trims trailing dots/spaces (problematic on some filesystems);
    - enforces a maximum length;
    - falls back to a safe placeholder if nothing usable remains.
    """
    value = unicodedata.normalize("NFC", value or "")
    value = _UNSAFE_CHARS.sub("_", value)
    value = value.strip().strip(".")
    value = value.replace("\x00", "")

    if not value or value in {".", ".."}:
        value = fallback

    if value.upper() in _RESERVED_NAMES:
        value = f"_{value}"

    if len(value) > _MAX_COMPONENT_LENGTH:
        value = value[:_MAX_COMPONENT_LENGTH].rstrip()

    return value or fallback


def sanitize_relative_path(parts: list[str], *, filename: str, extension: str) -> str:
    """Build a safe, relative `dir/subdir/filename.ext` path from untrusted parts.

    The result never contains `..` or an absolute path, so it is always safe
    to join onto MUSIC_ROOT (or any base directory) without a traversal risk,
    regardless of what the caller passes in.
    """
    safe_parts = [sanitize_path_component(p) for p in parts if p and p.strip()]
    safe_filename = sanitize_path_component(filename, fallback="track")
    safe_extension = re.sub(r"[^a-zA-Z0-9]", "", extension or "").lower() or "bin"
    return "/".join([*safe_parts, f"{safe_filename}.{safe_extension}"])
