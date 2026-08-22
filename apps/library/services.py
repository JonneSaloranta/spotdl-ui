"""Finalize a freshly-downloaded audio file into the permanent library.

Implements the atomic finalization sequence from CLAUDE.md #21:
download temp file -> hash -> metadata -> duplicate check -> move to final
path -> database commit. The final music file is never partially written:
it is moved into place only after it fully exists at a temporary path —
see _atomic_move() for what "moved into place" means when MUSIC_ROOT and
DOWNLOAD_TEMP_ROOT turn out not to share a filesystem after all.
"""

from __future__ import annotations

import errno
import logging
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from apps.downloader.models import DownloadItem, DuplicateStatus
from apps.library.filenames import sanitize_relative_path
from apps.library.hashing import sha256_file
from apps.library.models import DuplicateMatch, TrackFile
from apps.library.normalization import normalize_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FinalizeResult:
    track_file: TrackFile
    duplicate_status: str
    is_duplicate_skip: bool


def finalize_download(temp_file: Path, *, item: DownloadItem) -> FinalizeResult:
    """Hash, deduplicate, and permanently store a downloaded track.

    `item` supplies the metadata spotDL/our resolution step already
    captured (title/artist/album); we do not re-derive it from the
    filename. Runs duplicate detection levels 1 (exact hash) and 2
    (normalized artist+title) from CLAUDE.md #7; source-identifier
    matching happens earlier, before a download is even attempted — see
    find_existing_download() below.

    A level-2 ("probable") match may also trigger an automatic
    replacement of the older file — see
    _should_replace_with_better_version() — if it's judged a genuine
    upgrade rather than a possibly-different recording.
    """
    temp_file = Path(temp_file)
    digest = sha256_file(temp_file)
    size = temp_file.stat().st_size

    site_settings = _load_duplicate_policy()

    exact_match = TrackFile.objects.filter(sha256=digest, removed_at__isnull=True).first()
    normalized_artist = normalize_text(item.artist)
    normalized_title = normalize_text(item.title)

    if exact_match is not None:
        _record_duplicate(exact_match, digest, DuplicateMatch.MatchType.EXACT_HASH)
        if site_settings == "skip":
            temp_file.unlink(missing_ok=True)
            return FinalizeResult(
                track_file=exact_match,
                duplicate_status=DuplicateStatus.EXACT_DUPLICATE,
                is_duplicate_skip=True,
            )
        # keep_both / ask: still store the file (never silently overwrite
        # or silently discard — CLAUDE.md #7/#21), just flag it.
        track_file = _store_new_file(
            temp_file, item, digest, size, normalized_artist, normalized_title, disambiguate=True,
        )
        return FinalizeResult(
            track_file=track_file, duplicate_status=DuplicateStatus.EXACT_DUPLICATE, is_duplicate_skip=False,
        )

    probable_match = None
    if normalized_artist and normalized_title:
        probable_match = (
            TrackFile.objects.filter(
                normalized_artist=normalized_artist,
                normalized_title=normalized_title,
                removed_at__isnull=True,
            )
            .first()
        )

    track_file = _store_new_file(temp_file, item, digest, size, normalized_artist, normalized_title)

    if probable_match is not None:
        match = _record_duplicate(probable_match, digest, DuplicateMatch.MatchType.METADATA, track_file=track_file)
        if _should_replace_with_better_version(old=probable_match, new=track_file):
            _replace_track_file(old=probable_match, new=track_file)
            match.resolution = DuplicateMatch.Resolution.REPLACED
            match.save(update_fields=["resolution"])
        return FinalizeResult(
            track_file=track_file, duplicate_status=DuplicateStatus.PROBABLE_DUPLICATE, is_duplicate_skip=False,
        )

    return FinalizeResult(track_file=track_file, duplicate_status=DuplicateStatus.NEW, is_duplicate_skip=False)


def find_existing_download(*, source_identifier: str, source_url: str) -> TrackFile | None:
    """Return the TrackFile from an earlier successful download of the same
    Spotify source, if one still exists — so a track that already showed up
    in a previous import never gets downloaded again just because it's part
    of a *new* playlist/submission too (CLAUDE.md #7 duplicate-check level
    3, "same source identifier where available" — the one level that
    wasn't actually implemented yet, unlike exact-hash and metadata
    matching in finalize_download() above).

    Deliberately checked before ever running spotDL at all (see
    apps.downloader.tasks.resolve_batch_task), not after downloading like
    the other duplicate levels: those exist to catch a duplicate we
    couldn't have known about in advance (a different source producing the
    same audio); this one is the case where we already know in advance,
    from the source itself, that it's the same track — so there's no
    reason to spend a download attempt (and provider quota) confirming
    that again.

    Matches by `source_identifier` (the stable Spotify track ID) when
    available, and by `source_url` too either way — belt and suspenders
    for the rare save-file entry with no identifier. Only ever matches a
    *finished* item whose file is still active: if it was later removed
    (manual duplicate cleanup, replaced by a better version, ...) this
    returns None so the track downloads again instead of silently staying
    missing forever.
    """
    from apps.downloader.models import ItemStatus

    identity = Q()
    if source_identifier:
        identity |= Q(source_identifier=source_identifier)
    if source_url:
        identity |= Q(source_url=source_url)
    if not identity:
        return None

    item = (
        DownloadItem.objects.filter(identity)
        .filter(
            status__in=[ItemStatus.COMPLETED, ItemStatus.DUPLICATE_SKIPPED],
            result_file__isnull=False,
            result_file__removed_at__isnull=True,
        )
        .select_related("result_file")
        .order_by("-completed_at")
        .first()
    )
    return item.result_file if item else None


def _load_duplicate_policy() -> str:
    from apps.core.models import SiteSettings

    return SiteSettings.load().default_duplicate_policy


def _atomic_move(src: Path, dest: Path) -> None:
    """Move `src` to `dest`, atomically whenever possible.

    Tries `os.replace()` first — a single atomic rename, but only valid
    when both paths are on the same filesystem. Falls back to copying
    into a temp file *on the destination's filesystem* and renaming that
    into place when they're not: this happened for real when
    DOWNLOAD_TEMP_ROOT and MUSIC_ROOT turned out to be separate Docker
    volumes (`OSError: [Errno 18] Invalid cross-device link`), despite
    that being exactly what CLAUDE.md #21's atomicity guarantee assumes
    doesn't happen. Fixed at the infrastructure level too (see
    docker-compose.yml) but this fallback means a future deployment with
    the same split — a different orchestrator, separate PVCs, whatever —
    degrades to "works, with an extra copy" instead of "breaks every
    download's finalization step silently."

    Either path, the final destination is never partially written: the
    temp file only ever becomes visible at `dest` via a single rename,
    once it's fully copied and fsynced. A crash mid-copy leaves an
    orphaned temp file next to `dest`, never a truncated `dest` itself.
    """
    try:
        os.replace(src, dest)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    fd, tmp_name = tempfile.mkstemp(dir=dest.parent, prefix=f".{dest.name}.", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as tmp_file, open(src, "rb") as source_file:
            shutil.copyfileobj(source_file, tmp_file)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())
        os.replace(tmp_name, dest)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    src.unlink(missing_ok=True)


def _record_duplicate(
    existing: TrackFile, digest: str, match_type: str, *, track_file: TrackFile | None = None,
) -> DuplicateMatch:
    return DuplicateMatch.objects.create(
        original=existing,
        duplicate_of=track_file or existing,
        match_type=match_type,
        confidence=1.0 if match_type == DuplicateMatch.MatchType.EXACT_HASH else 0.7,
        resolution=DuplicateMatch.Resolution.PENDING,
    )


def _should_replace_with_better_version(*, old: TrackFile, new: TrackFile) -> bool:
    """Decide whether `new` should replace `old` when they're a
    "probable" (metadata-only) duplicate — same normalized artist+title,
    different audio content, per finalize_download() above.

    Deliberately narrow — this implements exactly what was asked for,
    not a general "pick the best version" heuristic: replace only when
    `new` is bigger (a reasonable proxy for higher bitrate/quality of
    what's presumably the same recording) or was sourced from YouTube
    Music specifically (`source_provider`, see DownloadItem/TrackFile).
    Plain YouTube (as opposed to YouTube Music) is deliberately excluded
    from that second condition: a plain YouTube search for the same
    title/artist is more likely to surface a live performance, which is
    a genuinely different recording, not just a lower-quality copy of
    the studio one — auto-replacing on that basis would risk silently
    losing real content, not just reclaiming disk space.

    Gated behind SiteSettings.auto_replace_probable_duplicates so this
    can be turned off without a code change.
    """
    from apps.core.models import SiteSettings

    if not SiteSettings.load().auto_replace_probable_duplicates:
        return False
    return new.size > old.size or new.source_provider == "youtube-music"


def _replace_track_file(*, old: TrackFile, new: TrackFile) -> None:
    """Soft-delete `old` and remove its file from disk because `new` has
    been judged a better version of the same track — see
    _should_replace_with_better_version(). Same soft-delete-the-row,
    actually-delete-the-file split as remove_exact_duplicates() below;
    see that function's docstring for why deleting the file itself is
    correct here rather than something CLAUDE.md #23 warns against.
    """
    file_path = Path(settings.MUSIC_ROOT) / old.path
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        logger.exception("Could not delete replaced file %s", file_path)
        return
    old.removed_at = timezone.now()
    old.save(update_fields=["removed_at"])


def _store_new_file(
    temp_file: Path,
    item: DownloadItem,
    digest: str,
    size: int,
    normalized_artist: str,
    normalized_title: str,
    *,
    disambiguate: bool = False,
) -> TrackFile:
    extension = temp_file.suffix.lstrip(".") or settings.SPOTDL_AUDIO_FORMAT
    filename_stem = item.title or "track"
    if disambiguate:
        filename_stem = f"{filename_stem} ({digest[:8]})"

    # Flat library: every file lands directly in MUSIC_ROOT, no artist/album
    # subfolders — requested directly, in preference to CLAUDE.md #22's
    # suggested "Artist/Album/Track" default. Collisions (two different
    # tracks that sanitize to the same filename) are already handled safely
    # below regardless of nesting: never overwritten, disambiguated with a
    # short hash suffix instead.
    relative_path = sanitize_relative_path([], filename=filename_stem, extension=extension)

    final_path = Path(settings.MUSIC_ROOT) / relative_path
    final_path.parent.mkdir(parents=True, exist_ok=True)

    if final_path.exists():
        # Extremely unlikely given the sha256-suffixed disambiguation
        # above, but never overwrite silently (CLAUDE.md #21). Re-derive
        # relative_path from the renamed final_path so the DB row always
        # matches the file actually on disk.
        final_path = final_path.with_name(f"{final_path.stem}-{digest[:8]}{final_path.suffix}")
        relative_path = str(final_path.relative_to(settings.MUSIC_ROOT))
    _atomic_move(temp_file, final_path)

    duration, bitrate = _read_audio_properties(final_path)

    with transaction.atomic():
        track_file = TrackFile.objects.create(
            path=relative_path,
            filename=final_path.name,
            size=size,
            sha256=digest,
            duration=duration,
            bitrate=bitrate,
            format=extension,
            mime_type=f"audio/{extension}",
            title=item.title,
            artist=item.artist,
            album=item.album,
            album_artist=item.album_artist,
            track_number=None,
            normalized_artist=normalized_artist,
            normalized_title=normalized_title,
            source_provider=item.source_provider,
        )
    return track_file


def _read_audio_properties(path: Path) -> tuple[float | None, int | None]:
    try:
        import mutagen

        audio = mutagen.File(path)
        if audio is None or audio.info is None:
            return None, None
        duration = getattr(audio.info, "length", None)
        bitrate = getattr(audio.info, "bitrate", None)
        return duration, bitrate
    except Exception:
        logger.exception("Could not read audio properties for %s", path)
        return None, None


def find_exact_duplicate_groups() -> list[list[TrackFile]]:
    """Group active TrackFiles by SHA-256 wherever more than one shares
    the same hash — byte-for-byte identical audio, the only kind of
    duplicate this app ever resolves automatically. A "probable"
    duplicate (same normalized artist+title, different hash — see
    finalize_download() above) might genuinely be a different recording
    (a remaster, a live version, ...), so those are left for a human to
    judge and never touched by remove_exact_duplicates().

    Each group is sorted oldest-first: index 0 is the copy
    remove_exact_duplicates() keeps, the rest are what it would remove.
    """
    duplicate_hashes = (
        TrackFile.objects.filter(removed_at__isnull=True)
        .values("sha256")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .values_list("sha256", flat=True)
    )
    return [
        list(TrackFile.objects.filter(sha256=sha256, removed_at__isnull=True).order_by("created_at", "id"))
        for sha256 in duplicate_hashes
    ]


def remove_exact_duplicates() -> tuple[int, int]:
    """Permanently delete every exact-duplicate TrackFile except the
    oldest copy of each (see find_exact_duplicate_groups()). Returns
    (files_removed, bytes_reclaimed).

    Soft-deletes the database row (`removed_at`, CLAUDE.md #17 — never a
    hard `.delete()`, so DuplicateMatch history and any
    DownloadItem.result_file reference stay intact) and also removes the
    underlying file from disk. That second part is deliberate: CLAUDE.md
    #23's "never delete a file just because its DB row disappeared" is
    about not letting deleting a *record* have that as an unintended side
    effect elsewhere — here it's the reverse and the entire point of this
    explicit, staff-initiated action is to actually reclaim the
    duplicated disk space, so leaving the file behind would defeat it.

    If a file genuinely can't be deleted (permissions, already gone
    unexpectedly, ...), that group's row is left alone rather than
    soft-deleting a database row for a file that's still physically
    present — losing track of a file that still exists would be worse
    than just not clearing it out this run.
    """
    removed = 0
    reclaimed = 0
    now = timezone.now()
    for group in find_exact_duplicate_groups():
        for track in group[1:]:
            file_path = Path(settings.MUSIC_ROOT) / track.path
            try:
                file_path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not delete duplicate file %s", file_path)
                continue
            reclaimed += track.size
            removed += 1
            track.removed_at = now
            track.save(update_fields=["removed_at"])
    return removed, reclaimed


# --------------------------------------------------------------------------
# Library filesystem scan (CLAUDE.md #6/#25): pick up audio files under
# MUSIC_ROOT that this app never itself downloaded — a restore from
# backup, a file copied in by hand, or anything else placed there outside
# the normal download pipeline. Run automatically once per `web` container
# start (docker/entrypoint.sh -> `manage.py queue_library_scan`) and
# on-demand from the navbar "Library sync" menu (staff only) or
# `manage.py scan_library`.
# --------------------------------------------------------------------------

_SCAN_AUDIO_EXTENSIONS = {"mp3", "flac", "ogg", "opus", "m4a", "wav"}  # matches spotdl's --format choices


@dataclass(frozen=True)
class ScanReport:
    files_found: int = 0
    tracks_added: int = 0
    tracks_removed: int = 0
    errors: int = 0


def scan_music_library(*, full: bool = False) -> ScanReport:
    """Add TrackFile rows for audio files under MUSIC_ROOT this app
    doesn't already know about ("quick sync"). With `full=True`
    ("full sync"), also soft-deletes any active TrackFile row whose file
    no longer exists on disk, and refreshes `last_scanned_at` on every
    row it verified is still present — a full sync is a genuine
    reconciliation against what's actually on disk, not just an
    additive scan, so it's deliberately not the automatic-on-startup
    default (see docker/entrypoint.sh): a MUSIC_ROOT that's temporarily
    unavailable at boot — an unmounted volume, a network filesystem
    hiccup — must never look like every file in it was deleted.

    Never deletes a *file* — unlike remove_exact_duplicates() and the
    probable-duplicate auto-replace in finalize_download(), which both
    delete a file *because a newer/better copy already replaces it*,
    this only ever removes a database row for a file this app can no
    longer find, and only in full-sync mode. A file a human placed in
    the library is never removed just because this function noticed it.

    New rows get the same SHA-256 hashing and exact/probable duplicate
    detection as a normal download (CLAUDE.md #7) — see
    _add_scanned_file() — but never the auto-replace behavior: a passive
    background scan silently deleting a file a human just put there
    would be a nasty surprise, so that stays limited to the download
    pipeline, where the "old" file is one this app produced itself.
    """
    root = Path(settings.MUSIC_ROOT)
    if not root.exists():
        logger.warning("Library scan: MUSIC_ROOT %s does not exist, skipping", root)
        return ScanReport()

    known = {t.path: t for t in TrackFile.objects.filter(removed_at__isnull=True)}
    seen_paths: set[str] = set()
    files_found = 0
    tracks_added = 0
    errors = 0
    now = timezone.now()

    for file_path in sorted(root.rglob("*")):
        if not file_path.is_file():
            continue
        if file_path.suffix.lstrip(".").lower() not in _SCAN_AUDIO_EXTENSIONS:
            continue

        files_found += 1
        relative_path = str(file_path.relative_to(root))
        seen_paths.add(relative_path)
        existing = known.get(relative_path)

        if existing is not None:
            if full:
                TrackFile.objects.filter(pk=existing.pk).update(last_scanned_at=now)
            continue

        try:
            _add_scanned_file(file_path, relative_path)
            tracks_added += 1
        except Exception:
            logger.exception("Could not import scanned file %s", file_path)
            errors += 1

    tracks_removed = 0
    if full:
        missing_paths = set(known) - seen_paths
        if missing_paths:
            tracks_removed = TrackFile.objects.filter(path__in=missing_paths).update(removed_at=now)

    return ScanReport(
        files_found=files_found, tracks_added=tracks_added, tracks_removed=tracks_removed, errors=errors,
    )


def _add_scanned_file(file_path: Path, relative_path: str) -> None:
    """Add one TrackFile row for a file scan_music_library() found that
    isn't in the database yet. The file is already in its permanent
    location (unlike _store_new_file(), nothing is moved) — this only
    ever reads it.
    """
    digest = sha256_file(file_path)
    size = file_path.stat().st_size
    extension = file_path.suffix.lstrip(".").lower()
    duration, bitrate = _read_audio_properties(file_path)
    tags = _extract_scan_tags(file_path)

    normalized_artist = normalize_text(tags.get("artist", ""))
    normalized_title = normalize_text(tags.get("title", ""))

    exact_match = TrackFile.objects.filter(sha256=digest, removed_at__isnull=True).first()

    track_file = TrackFile.objects.create(
        path=relative_path,
        filename=file_path.name,
        size=size,
        sha256=digest,
        duration=duration,
        bitrate=bitrate,
        format=extension,
        mime_type=f"audio/{extension}",
        title=tags.get("title", ""),
        artist=tags.get("artist", ""),
        album=tags.get("album", ""),
        album_artist=tags.get("album_artist", ""),
        track_number=tags.get("track_number"),
        disc_number=tags.get("disc_number"),
        year=tags.get("year"),
        normalized_artist=normalized_artist,
        normalized_title=normalized_title,
        last_scanned_at=timezone.now(),
    )

    if exact_match is not None:
        _record_duplicate(exact_match, digest, DuplicateMatch.MatchType.EXACT_HASH, track_file=track_file)
    elif normalized_artist and normalized_title:
        probable_match = (
            TrackFile.objects.filter(
                normalized_artist=normalized_artist, normalized_title=normalized_title, removed_at__isnull=True,
            )
            .exclude(pk=track_file.pk)
            .first()
        )
        if probable_match is not None:
            _record_duplicate(probable_match, digest, DuplicateMatch.MatchType.METADATA, track_file=track_file)


def _extract_scan_tags(path: Path) -> dict:
    """Best-effort metadata straight from a file's own tags, for a
    scanned file that has no DownloadItem to supply it from. Uses
    mutagen's format-agnostic "easy" tag interface (uniform keys across
    ID3/MP4/FLAC/OGG) — a different mutagen entry point than
    coverart.py's raw per-format access, which needs the one field
    ("embedded picture") that easy mode doesn't expose.
    """
    try:
        import mutagen

        audio = mutagen.File(path, easy=True)
        if audio is None:
            return {}

        def first(key: str) -> str:
            values = audio.get(key) or []
            return str(values[0]) if values else ""

        return {
            "title": first("title"),
            "artist": first("artist"),
            "album": first("album"),
            "album_artist": first("albumartist") or first("artist"),
            "track_number": _safe_int(first("tracknumber").split("/")[0]),
            "disc_number": _safe_int(first("discnumber").split("/")[0]),
            "year": _safe_int(first("date")[:4]),
        }
    except Exception:
        logger.exception("Could not read tags for %s", path)
        return {}


def _safe_int(value: str) -> int | None:
    try:
        return int(value) if value else None
    except (TypeError, ValueError):
        return None
