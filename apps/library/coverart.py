"""Read a track's embedded cover image directly from its audio tags.

Deliberately not extracted to a separate file at finalization time — cover
art is small, and reading it on demand avoids managing a second on-disk
asset (and its own cleanup/retention rules) that could drift out of sync
with the audio file's own tags. See apps/library/views.track_cover.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def extract_cover_art(path: Path) -> tuple[bytes, str] | None:
    """Return (image_bytes, mime_type) for the file's embedded cover, or None.

    Covers the tag formats spotDL actually produces in practice (ID3 for
    mp3, the default output format; FLAC and MP4/M4A as a bonus for
    anyone who has changed SPOTDL_AUDIO_FORMAT). Anything else, or a file
    with no embedded picture at all, is treated the same as "no cover" —
    callers fall back to a placeholder rather than erroring.
    """
    try:
        import mutagen
        from mutagen.flac import FLAC
        from mutagen.id3 import ID3
        from mutagen.mp4 import MP4

        audio = mutagen.File(path)
        if audio is None:
            return None

        if isinstance(audio.tags, ID3):
            frames = audio.tags.getall("APIC")
            if frames:
                return bytes(frames[0].data), frames[0].mime or "image/jpeg"

        if isinstance(audio, FLAC) and audio.pictures:
            picture = audio.pictures[0]
            return bytes(picture.data), picture.mime or "image/jpeg"

        if isinstance(audio, MP4) and audio.tags and "covr" in audio.tags:
            covers = audio.tags["covr"]
            if covers:
                cover = covers[0]
                mime = "image/png" if cover.imageformat == cover.FORMAT_PNG else "image/jpeg"
                return bytes(cover), mime
    except Exception:
        logger.exception("Could not extract cover art from %s", path)
    return None
