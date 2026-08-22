"""MusicBrainz client (CLAUDE.md #9).

MusicBrainz's usage policy requires a maximum of ~1 request/second and a
descriptive User-Agent identifying the application and a contact method.
This module enforces both, caches results in MusicBrainzRecording so the
same query is never re-fetched, and retries transient failures with
backoff. It never overwrites metadata by itself — callers get back
candidate matches for a human (or an explicit "apply" call) to accept.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.musicbrainz.models import MusicBrainzRecording

logger = logging.getLogger(__name__)

_RATE_LIMIT_CACHE_KEY = "musicbrainz:last_request_at"
_MAX_RETRIES = 3
_BACKOFF_BASE_SECONDS = 1.5

# MusicBrainz's own 0-100 relevance score for a search result. Only a match
# at or above this is ever accepted *automatically* (post-download and by
# the background sweep) — anything less confident is left for a human to
# resolve through the manual search/preview/apply page instead, which has
# no such threshold since a person is looking at the actual candidates.
_AUTO_ENRICH_MIN_SCORE = 90


class MusicBrainzError(Exception):
    """Base class for MusicBrainz client errors."""


class MusicBrainzConnectionError(MusicBrainzError):
    """The MusicBrainz API could not be reached after retries."""


@dataclass(frozen=True)
class RecordingCandidate:
    mbid: str
    title: str
    artist: str
    artist_mbid: str
    release: str
    release_mbid: str
    length_ms: int | None
    score: int
    isrcs: list[str] = field(default_factory=list)


class MusicBrainzClient:
    def __init__(
        self,
        base_url: str | None = None,
        user_agent: str | None = None,
        timeout: int | None = None,
        rate_limit_seconds: float | None = None,
    ):
        self.base_url = (base_url or settings.MUSICBRAINZ_BASE_URL).rstrip("/")
        contact = settings.MUSICBRAINZ_CONTACT
        agent = user_agent or settings.MUSICBRAINZ_USER_AGENT
        self.user_agent = f"{agent} ( {contact} )" if contact else agent
        self.timeout = timeout or 10
        self.rate_limit_seconds = rate_limit_seconds if rate_limit_seconds is not None else settings.MUSICBRAINZ_RATE_LIMIT

    def _throttle(self) -> None:
        """Block just long enough to keep requests at most one every
        `rate_limit_seconds` apart, tracked in the shared cache so multiple
        worker processes still respect the same global limit."""
        last = cache.get(_RATE_LIMIT_CACHE_KEY)
        now = time.monotonic()
        if last is not None:
            elapsed = now - last
            wait = self.rate_limit_seconds - elapsed
            if wait > 0:
                time.sleep(wait)
        cache.set(_RATE_LIMIT_CACHE_KEY, time.monotonic(), timeout=60)

    def _get(self, path: str, params: dict) -> dict:
        url = f"{self.base_url}/{path}"
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}

        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            self._throttle()
            try:
                response = requests.get(url, params=params, headers=headers, timeout=self.timeout)
                if response.status_code == 503:
                    # MusicBrainz signals rate-limit backpressure with 503.
                    raise requests.exceptions.RequestException("503 rate limited")
                response.raise_for_status()
                return response.json()
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < _MAX_RETRIES:
                    backoff = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
                    logger.warning("MusicBrainz request failed (attempt %d/%d): %s", attempt, _MAX_RETRIES, exc)
                    time.sleep(backoff)

        raise MusicBrainzConnectionError(f"MusicBrainz request to {path!r} failed after {_MAX_RETRIES} attempts") from last_exc

    def search_recordings(
        self, *, title: str, artist: str = "", album: str = "", limit: int = 10,
    ) -> list[RecordingCandidate]:
        """Search for recordings matching title/artist/album.

        Results are not cached individually (a search is a many-candidate
        query); `get_recording()` caches the canonical per-MBID lookup
        that a caller uses once a candidate is chosen.
        """
        query_parts = [f'recording:"{title}"']
        if artist:
            query_parts.append(f'artist:"{artist}"')
        if album:
            query_parts.append(f'release:"{album}"')
        query = " AND ".join(query_parts)

        data = self._get("recording", {"query": query, "limit": limit, "fmt": "json"})
        return [_parse_candidate(entry) for entry in data.get("recordings", [])]

    def get_recording(self, mbid: str, *, use_cache: bool = True) -> MusicBrainzRecording:
        """Fetch (and cache) full detail for a specific recording MBID."""
        if use_cache:
            cached = MusicBrainzRecording.objects.filter(mbid=mbid).first()
            if cached is not None:
                return cached

        data = self._get(f"recording/{mbid}", {"inc": "artist-credits+releases+isrcs", "fmt": "json"})
        return _store_recording(data)


def _parse_candidate(entry: dict) -> RecordingCandidate:
    artist_credit = (entry.get("artist-credit") or [{}])[0]
    releases = entry.get("releases") or [{}]
    release = releases[0] if releases else {}
    return RecordingCandidate(
        mbid=str(entry.get("id", "")),
        title=str(entry.get("title", "")),
        artist=str(artist_credit.get("name", "")),
        artist_mbid=str((artist_credit.get("artist") or {}).get("id", "")),
        release=str(release.get("title", "")),
        release_mbid=str(release.get("id", "")),
        length_ms=_safe_int(entry.get("length")),
        score=_safe_int(entry.get("score")) or 0,
        isrcs=list(entry.get("isrcs") or []),
    )


def _store_recording(data: dict) -> MusicBrainzRecording:
    artist_credit = (data.get("artist-credit") or [{}])[0]
    releases = data.get("releases") or [{}]
    release = releases[0] if releases else {}

    recording, _created = MusicBrainzRecording.objects.update_or_create(
        mbid=data["id"],
        defaults={
            "title": data.get("title", ""),
            "artist": artist_credit.get("name", ""),
            "artist_mbid": (artist_credit.get("artist") or {}).get("id", ""),
            "release": release.get("title", ""),
            "release_mbid": release.get("id", ""),
            "length_ms": _safe_int(data.get("length")),
            "isrcs": list(data.get("isrcs") or []),
            "raw_response": data,
        },
    )
    return recording


def _safe_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def auto_enrich_track(track_file, *, client: MusicBrainzClient | None = None) -> bool:
    """Best-effort, automatic MusicBrainz lookup for one track.

    Used both right after a track finishes downloading and by the
    background sweep for tracks never checked before (CLAUDE.md #9). Only
    ever *fills in* missing information — the MusicBrainz ID, and the
    album title if it was blank — never overwrites title/artist/album
    that are already populated ("never blindly overwrite user-approved
    metadata"). Deliberately overwriting already-populated fields still
    only happens through the manual per-track review/apply page.

    Always marks `track_file.musicbrainz_checked_at`, whether or not a
    confident match was found, so a track that genuinely has no good
    MusicBrainz match doesn't get looked up again on every sweep run.

    Returns True if a confident match was found (regardless of whether it
    actually changed anything — e.g. the track already had this exact
    musicbrainz_id from an earlier check).
    """
    client = client or MusicBrainzClient()
    found = False

    if track_file.title and track_file.artist:
        try:
            candidates = client.search_recordings(
                title=track_file.title, artist=track_file.artist, album=track_file.album,
            )
        except MusicBrainzError as exc:
            logger.info("Auto-enrich: MusicBrainz search failed for track %s: %s", track_file.pk, exc)
            candidates = []

        best = max(candidates, key=lambda c: c.score, default=None)
        if best is not None and best.score >= _AUTO_ENRICH_MIN_SCORE:
            try:
                recording = client.get_recording(best.mbid)
            except MusicBrainzError as exc:
                logger.info("Auto-enrich: MusicBrainz lookup failed for track %s: %s", track_file.pk, exc)
                recording = None
            if recording is not None:
                found = True
                update_fields = []
                if not track_file.musicbrainz_id:
                    track_file.musicbrainz_id = recording.mbid
                    update_fields.append("musicbrainz_id")
                if not track_file.album and recording.release:
                    track_file.album = recording.release
                    update_fields.append("album")
                if update_fields:
                    track_file.save(update_fields=update_fields)
                if recording.matched_track_file_id is None:
                    recording.matched_track_file = track_file
                    recording.save(update_fields=["matched_track_file"])

    track_file.musicbrainz_checked_at = timezone.now()
    track_file.save(update_fields=["musicbrainz_checked_at"])
    return found
