"""spotDL subprocess integration (CLAUDE.md #4).

All spotDL invocation lives here — nowhere else in the codebase should
build a spotDL command line or parse its output. Everything is built as an
argument array and run with ``shell=False``; nothing from user input is
ever interpolated into a shell string.

Command syntax verified against the actually-installed package
(``spotdl==4.5.2``, see requirements/base.txt) by running ``spotdl --help``,
``spotdl download --help`` and ``spotdl save --help`` — not guessed. The
JSON schema of ``.spotdl`` save files below is documented from spotDL's
public `Song` dataclass but could **not** be exercised against a real
Spotify response in this environment (outbound access to Spotify's API was
not reachable from the sandbox this was developed in). Parsing is therefore
defensive (every field read with ``.get()`` and a safe default) and must be
re-verified against a real ``spotdl save`` run before relying on it in
production — see docs/SPOTDL_VERIFICATION.md.
"""

from __future__ import annotations

import json
import logging
import os
import re
import select
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from django.conf import settings

logger = logging.getLogger(__name__)


class SpotDLError(Exception):
    """Base class for spotDL service errors."""


class SpotDLTimeoutError(SpotDLError):
    """The spotDL process exceeded its allotted time budget and was killed."""


class SpotDLCancelledError(SpotDLError):
    """The caller's `should_cancel` callback requested the process be stopped."""


class SpotDLResolveError(SpotDLError):
    """spotDL could not resolve the given source into a track list."""


@dataclass(frozen=True)
class ResolvedTrack:
    """One track discovered while resolving a source URL (CLAUDE.md #5 step 5)."""

    source_identifier: str  # e.g. Spotify track id
    source_url: str
    title: str
    artist: str
    album: str
    album_artist: str
    duration_seconds: float | None
    track_number: int | None
    disc_number: int | None
    isrc: str | None
    playlist_name: str = ""
    playlist_position: int | None = None


@dataclass(frozen=True)
class DownloadOutcome:
    """Result of running a single-track download."""

    success: bool
    exit_code: int
    file_path: Path | None
    stdout_tail: str
    stderr_tail: str
    error_message: str = ""


@dataclass(frozen=True)
class ProcessRunResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False


_OUTPUT_TAIL_CHARS = 8000  # cap captured output so a runaway process cannot blow up memory/DB rows


def _base_args() -> list[str]:
    """Flags shared by every spotDL invocation.

    Only ever holds flags with a fixed number of values (0 or 1) — safe to
    place before the positional `{operation} query` arguments regardless
    of argparse's parsing order. `--audio` is deliberately not here: it
    takes a variable number of values (`nargs='*'`, per `spotdl --help`),
    so placed before a positional it greedily swallows the *next*
    argument too (observed for real: `--audio soundcloud save <url>` was
    parsed as `--audio soundcloud save` — "save" treated as an invalid
    extra provider choice — see _trailing_provider_args()).
    """
    args = [
        settings.SPOTDL_EXECUTABLE,
        "--headless",  # never prompt interactively; we drive it entirely by flags
        "--log-level", "INFO",
        # One plain line per stage ("<title>: Downloading", ...) instead of
        # a redrawing progress bar that relies on carriage returns and
        # terminal control codes — both easier to read in a captured log
        # and the only reliably parseable form for the live progress
        # updates in _detect_stage_progress()/_run() below.
        "--simple-tui",
        "--format", settings.SPOTDL_AUDIO_FORMAT,
        "--max-retries", str(settings.SPOTDL_MAX_RETRIES),
    ]
    if settings.SPOTDL_BITRATE:
        # Empty means "let spotDL use its own default" — its own explicit
        # way to remove the constraint entirely is SPOTDL_BITRATE=disable
        # (uses the source's original bitrate rather than transcoding to a
        # fixed one; for m4a/opus output it skips conversion altogether —
        # see `spotdl --help`), not simply omitting this flag, since
        # spotDL's internal default if unspecified isn't documented as
        # "no constraint".
        args += ["--bitrate", settings.SPOTDL_BITRATE]
    if settings.SPOTDL_CLIENT_ID and settings.SPOTDL_CLIENT_SECRET:
        args += ["--client-id", settings.SPOTDL_CLIENT_ID, "--client-secret", settings.SPOTDL_CLIENT_SECRET]
    return args


def _trailing_provider_args(audio_providers: list[str] | None = None) -> list[str]:
    """Flags that must come after every positional argument (see
    _base_args() docstring for why `--audio` in particular can't go
    before them).

    `audio_providers`, when given, overrides settings.SPOTDL_PRIMARY_AUDIO_PROVIDER
    for this one invocation — used to drive the primary-then-fallback
    provider tiering in apps.downloader.tasks (CLAUDE.md-adjacent: spotDL's
    own `--audio p1 p2` fallback tries each provider once per invocation,
    it doesn't retry p1 several times before moving on, so that tiering
    has to be driven by which providers *we* pass on each separate retry
    attempt instead).
    """
    args: list[str] = []
    if audio_providers:
        args += ["--audio", *audio_providers]
    if settings.SPOTDL_PROXY:
        # Some deployments' outbound IP gets blocked/rate-limited by the
        # audio provider (YouTube in particular enforces this increasingly
        # aggressively against datacenter/cloud IP ranges — see
        # docs/TROUBLESHOOTING.md) independent of whether spotDL/yt-dlp
        # itself is configured correctly. A proxy with a non-blocked exit
        # IP is the only real lever for that; nothing in this app's own
        # code can compensate for it.
        args += ["--proxy", settings.SPOTDL_PROXY]
    return args


def build_resolve_command(source_url: str, save_file: Path) -> list[str]:
    """Build the argument array that discovers tracks for `source_url`
    without downloading audio (CLAUDE.md #5 steps 4-5)."""
    return [
        *_base_args(),
        "save",
        source_url,
        "--save-file", str(save_file),
        *_trailing_provider_args(),
    ]


def build_download_command(
    source_url: str, output_dir: Path, *, audio_providers: list[str] | None = None,
) -> list[str]:
    """Build the argument array that downloads a single track.

    `output_dir` must already be an absolute, server-controlled path
    (DOWNLOAD_TEMP_ROOT/<job>/), never a user-supplied path — the
    `--output` template only controls the filename *within* that
    directory (docs/SECURITY.md: never let metadata create arbitrary
    filesystem paths).

    `audio_providers`, when given, restricts this one attempt to those
    provider(s) — see apps.downloader.tasks.pick_audio_providers() for how
    callers tier "try YouTube a few times, then fall back" across retries.
    Falls back to settings.SPOTDL_PRIMARY_AUDIO_PROVIDER when omitted.
    """
    if audio_providers is None and settings.SPOTDL_PRIMARY_AUDIO_PROVIDER:
        audio_providers = [settings.SPOTDL_PRIMARY_AUDIO_PROVIDER]

    output_template = str(Path(output_dir) / settings.SPOTDL_OUTPUT_TEMPLATE)
    return [
        *_base_args(),
        "download",
        source_url,
        "--output", output_template,
        "--overwrite", "skip",  # never silently overwrite (CLAUDE.md #7)
        "--print-errors",
        "--threads", "1",  # one worker task = one track; concurrency is controlled by Celery
        *_trailing_provider_args(audio_providers),
    ]


_POLL_INTERVAL = 1.0  # seconds between cancellation/timeout checks while a process runs
_HEARTBEAT_INTERVAL = 30.0  # seconds between "still running" log lines for a long-lived process

# Progress reported via `--simple-tui`'s one-line-per-stage output
# ("<title>: <stage>"), observed for real against the installed spotdl
# binary (see docs/SPOTDL_VERIFICATION.md) — spotDL/yt-dlp don't expose
# byte-level download progress in any reliably parseable form even with
# --simple-tui, so these percentages are a rough estimate of typical time
# spent per stage, not literal bytes transferred. Deliberately stops
# short of 80: apps.downloader.tasks.download_item_task already sets 80%
# itself right after this whole call returns successfully, so these only
# need to cover the gap between the 10% set before it starts and that.
_STAGE_PROGRESS: list[tuple[str, int]] = [
    ("Searching for song", 20),
    ("Getting audio meta", 35),
    ("Downloading", 50),
    ("Embedding metadata", 70),
]


def _detect_stage_progress(line: str) -> tuple[int, str] | None:
    """Match one line of `--simple-tui` output against a known stage.

    Returns (percent, stage_text) for the first recognized stage found in
    `line`, or None for a line that isn't a per-track stage update at all
    (spotDL also prints "Processing query: ...", the final "Downloaded
    ..." summary, warnings, etc.) or names a stage this list doesn't
    recognize — a future spotDL version renaming a stage degrades to "no
    live update for that one stage", never an error, matching this
    module's existing defensive parsing (see module docstring).
    """
    for stage_text, percent in _STAGE_PROGRESS:
        if stage_text in line:
            return percent, stage_text
    return None


def _run(
    args: list[str],
    *,
    timeout: int,
    cwd: Path | None = None,
    should_cancel: Callable[[], bool] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
    on_line: Callable[[str], None] | None = None,
) -> ProcessRunResult:
    """Run a spotDL subprocess and capture its output.

    Uses `shell=False` with an argument array throughout, per CLAUDE.md
    design principle #2/#3 — nothing here is ever built from a shell
    string, so there is no injection surface regardless of what a source
    URL or track title contains.

    Reads stdout/stderr as they arrive (via `select`) rather than only
    once the process exits, so `on_progress` — when given — can be called
    with live stage updates as spotDL reports them (see
    _detect_stage_progress()). The same loop iteration also checks
    `should_cancel` and the timeout budget every `_POLL_INTERVAL` seconds,
    so a stuck download still doesn't tie up a Celery worker slot
    indefinitely (CLAUDE.md #4: "support cancellation") — that part is
    unchanged from before this needed to read output incrementally.

    `on_line`, when given, is called with every complete stdout line,
    unfiltered — unlike `on_progress`, which only fires for the small set
    of lines `_detect_stage_progress()` recognizes as a download stage.
    Resolving a source (`resolve_source()` below) has no such stages to
    report progress against, so before this there was no visibility at
    all into a resolve still in progress — only ever a final success or
    timeout, minutes later. `on_line` exists so a caller can log spotDL's
    own output as it happens instead (see resolve_source()'s use of it) —
    it deliberately never touches the database itself, unlike
    `on_progress`, since raw log lines aren't something any caller has
    needed to store as state, only to observe live.

    Also logs a "still running" heartbeat (with elapsed time and bytes
    read so far) every `_HEARTBEAT_INTERVAL` seconds regardless of
    `on_line`/`on_progress` — reproduced for real that a slow resolve
    otherwise produces *no* log output whatsoever for its entire
    duration, making it impossible to tell from the logs alone whether it
    was still working or already stuck (see docs/TROUBLESHOOTING.md).

    This all runs synchronously on the caller's own thread (no background
    reader thread) precisely so `on_progress` can safely touch the
    database directly — this is a Celery worker task's own thread, so
    Django's per-thread connection handling applies normally.
    """
    logger.info("Running spotDL: %s", " ".join(args[:4]) + " ...")
    try:
        proc = subprocess.Popen(
            args,
            shell=False,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise SpotDLError(f"spotDL executable not found: {settings.SPOTDL_EXECUTABLE}") from exc

    start = time.monotonic()
    last_heartbeat = start
    stdout_chunks: list[bytes] = []
    stderr_chunks: list[bytes] = []
    stdout_line_buffer = b""
    seen_stages: set[str] = set()

    def drain(ready: list) -> None:
        nonlocal stdout_line_buffer
        for stream in ready:
            data = os.read(stream.fileno(), 65536)
            if not data:
                continue
            if stream is proc.stdout:
                stdout_chunks.append(data)
                if on_progress is not None or on_line is not None:
                    stdout_line_buffer += data
                    *complete_lines, stdout_line_buffer = stdout_line_buffer.split(b"\n")
                    for raw_line in complete_lines:
                        text = raw_line.decode("utf-8", errors="replace")
                        if on_line is not None and text.strip():
                            on_line(text)
                        if on_progress is not None:
                            detected = _detect_stage_progress(text)
                            if detected is not None and detected[1] not in seen_stages:
                                seen_stages.add(detected[1])
                                on_progress(*detected)
            else:
                stderr_chunks.append(data)

    def collected() -> tuple[str, str]:
        stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace")
        stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace")
        return stdout[-_OUTPUT_TAIL_CHARS:], stderr[-_OUTPUT_TAIL_CHARS:]

    while True:
        open_streams = [s for s in (proc.stdout, proc.stderr) if not s.closed]
        ready, _, _ = select.select(open_streams, [], [], _POLL_INTERVAL) if open_streams else ([], [], [])
        drain(ready)

        if proc.poll() is not None:
            # The process may have exited with a final burst of output
            # still sitting in the pipes; select() can report that as
            # ready right up to EOF, so drain once more, non-blocking.
            ready, _, _ = select.select(open_streams, [], [], 0) if open_streams else ([], [], [])
            drain(ready)
            break

        if should_cancel is not None and should_cancel():
            logger.info("spotDL process cancelled by caller; terminating")
            _terminate(proc)
            stdout, stderr = collected()
            return ProcessRunResult(exit_code=proc.returncode or -1, stdout=stdout, stderr=stderr, cancelled=True)

        now = time.monotonic()
        if now - start > timeout:
            logger.warning("spotDL timed out after %ss", timeout)
            _terminate(proc)
            stdout, stderr = collected()
            return ProcessRunResult(exit_code=proc.returncode or -1, stdout=stdout, stderr=stderr, timed_out=True)

        if now - last_heartbeat >= _HEARTBEAT_INTERVAL:
            last_heartbeat = now
            logger.info(
                "spotDL still running after %.0fs (of %ds timeout budget); "
                "%d bytes stdout / %d bytes stderr read so far",
                now - start, timeout, sum(len(c) for c in stdout_chunks), sum(len(c) for c in stderr_chunks),
            )

    stdout, stderr = collected()
    return ProcessRunResult(exit_code=proc.returncode, stdout=stdout, stderr=stderr)


def _terminate(proc: subprocess.Popen) -> None:
    """Ask a process to exit gracefully, escalating to SIGKILL if needed."""
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def resolve_source(source_url: str, *, timeout: int | None = None) -> list[ResolvedTrack]:
    """Discover the individual tracks referenced by `source_url`.

    Works for single tracks, albums, and playlists alike — spotDL expands
    all of them into a flat song list in the save file.
    """
    # Deliberately its own setting, not SPOTDL_DOWNLOAD_TIMEOUT: resolving looks up
    # every track in the source before returning anything, so a large playlist takes
    # proportionally longer than downloading any single track does (see
    # SPOTDL_RESOLVE_TIMEOUT's definition in config/settings/base.py).
    timeout = timeout or settings.SPOTDL_RESOLVE_TIMEOUT
    start = time.monotonic()
    logger.info("Resolving %r (timeout budget %ds)", source_url, timeout)
    with tempfile.TemporaryDirectory(prefix="spotdl-resolve-") as tmpdir:
        save_file = Path(tmpdir) / "resolved.spotdl"
        args = build_resolve_command(source_url, save_file)
        # Logging every raw output line as spotDL prints it, not just recognized
        # stages — resolving a whole playlist has no stage markers to report
        # progress against the way a single download does, so before this,
        # nothing at all showed up in the worker logs for the entire duration of
        # a resolve (reproduced for real: a 150-track playlist took ~10-12
        # minutes with zero log output the whole time, making a slow-but-working
        # resolve indistinguishable from a hung one purely from the logs).
        result = _run(
            args, timeout=timeout,
            on_line=lambda line: logger.info("spotDL resolve output (%s): %s", source_url, line),
        )
        elapsed = time.monotonic() - start

        if result.timed_out:
            logger.warning("Resolving %r timed out after %.0fs", source_url, elapsed)
            raise SpotDLTimeoutError(f"Resolving {source_url!r} timed out after {timeout}s")
        if result.exit_code != 0 or not save_file.exists():
            logger.warning(
                "Resolving %r failed after %.0fs (exit code %s)", source_url, elapsed, result.exit_code,
            )
            raise SpotDLResolveError(
                f"spotDL could not resolve {source_url!r} (exit code {result.exit_code}): "
                f"{result.stderr.strip()[-500:]}"
            )

        try:
            raw = json.loads(save_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise SpotDLResolveError(f"Could not parse spotDL save file for {source_url!r}") from exc

    logger.info("Resolved %r in %.0fs: %d track(s) found", source_url, elapsed, len(raw))
    return [_parse_song(entry, fallback_source_url=source_url) for entry in raw]


def _parse_song(entry: dict, *, fallback_source_url: str) -> ResolvedTrack:
    """Parse one entry of a `.spotdl` save file into a ResolvedTrack.

    Every field is read defensively (see module docstring): an unexpected
    or missing key degrades to an empty/None value rather than raising, so
    a spotDL output-format change never takes down an entire batch.
    """
    return ResolvedTrack(
        source_identifier=str(entry.get("song_id") or entry.get("track_id") or ""),
        source_url=str(entry.get("url") or fallback_source_url),
        title=str(entry.get("name") or ""),
        artist=str(entry.get("artist") or ", ".join(entry.get("artists") or []) or ""),
        album=str(entry.get("album_name") or ""),
        album_artist=str(entry.get("album_artist") or ""),
        duration_seconds=_safe_float(entry.get("duration")),
        track_number=_safe_int(entry.get("track_number")),
        disc_number=_safe_int(entry.get("disc_number")),
        isrc=entry.get("isrc") or None,
        playlist_name=str(entry.get("list_name") or ""),
        playlist_position=_safe_int(entry.get("list_position")),
    )


def _safe_float(value) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_int(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def download_track(
    source_url: str,
    output_dir: Path,
    *,
    timeout: int | None = None,
    should_cancel: Callable[[], bool] | None = None,
    audio_providers: list[str] | None = None,
    on_progress: Callable[[int, str], None] | None = None,
) -> DownloadOutcome:
    """Download a single track into `output_dir`.

    Returns a DownloadOutcome describing success/failure; never raises for
    an ordinary download failure (bad match, geo-blocked, etc.) so callers
    can record it on the DownloadItem and continue the batch (CLAUDE.md
    #5: "A batch must remain useful even if some tracks fail"). Only
    infrastructure problems (timeout, cancellation, missing executable)
    raise.

    `audio_providers` restricts this one attempt to those provider(s) —
    see build_download_command(). `on_progress`, when given, is called
    with (percent, stage_text) as spotDL reports each download stage —
    see _detect_stage_progress()/_run().
    """
    timeout = timeout or settings.SPOTDL_DOWNLOAD_TIMEOUT
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    before = _snapshot_files(output_dir)
    args = build_download_command(source_url, output_dir, audio_providers=audio_providers)
    result = _run(args, timeout=timeout, should_cancel=should_cancel, on_progress=on_progress)

    if result.cancelled:
        raise SpotDLCancelledError(f"Downloading {source_url!r} was cancelled")
    if result.timed_out:
        raise SpotDLTimeoutError(f"Downloading {source_url!r} timed out after {timeout}s")

    after = _snapshot_files(output_dir)
    new_files = [p for p in after if p not in before]
    audio_files = [p for p in new_files if p.suffix.lstrip(".") == settings.SPOTDL_AUDIO_FORMAT]

    if result.exit_code == 0 and audio_files:
        return DownloadOutcome(
            success=True,
            exit_code=result.exit_code,
            file_path=audio_files[0],
            stdout_tail=result.stdout[-2000:],
            stderr_tail=result.stderr[-2000:],
        )

    return DownloadOutcome(
        success=False,
        exit_code=result.exit_code,
        file_path=None,
        stdout_tail=result.stdout[-2000:],
        stderr_tail=result.stderr[-2000:],
        error_message=_extract_error_message(result) or "spotDL did not produce an audio file",
    )


def _snapshot_files(directory: Path) -> set[Path]:
    if not directory.exists():
        return set()
    return {p for p in directory.rglob("*") if p.is_file()}


_EXCEPTION_LINE_RE = re.compile(r"^[A-Z][A-Za-z0-9]*Error:\s")


def _extract_error_message(result: ProcessRunResult) -> str:
    """Best-effort short human-readable error, from spotDL's own output.

    Prefers a line that looks like a Python exception repr
    ("LookupError: No results found for song: ...") over simply the last
    non-blank line. Reproduced for real: when a track can't be matched,
    spotDL prints that exact message twice — once on its own, unwrapped,
    and again as part of a longer "<url> - LookupError: message" summary
    line that its own console output (the `rich` library) word-wraps
    across the terminal width whenever the url pushes it over. Naively
    taking "the last line" then only picks up the tail fragment after
    the wrap ("found for song: ...", missing "No results" from the line
    above) — see docs/SPOTDL_VERIFICATION.md. Falls back to the previous
    last-non-blank-line behavior when no such line is present, so an
    unrecognized format still degrades gracefully instead of finding
    nothing at all.
    """
    for stream in (result.stderr, result.stdout):
        lines = [line.strip() for line in stream.splitlines() if line.strip()]
        if not lines:
            continue
        for line in reversed(lines):
            if _EXCEPTION_LINE_RE.match(line):
                return line[:500]
        return lines[-1][:500]
    return ""


def check_installed_version() -> str:
    """Return the installed spotDL version string, used by admin diagnostics
    to catch a silent version drift against SPOTDL_VERSION."""
    result = _run([settings.SPOTDL_EXECUTABLE, "--version"], timeout=15)
    if result.exit_code != 0:
        raise SpotDLError(f"Could not determine spotDL version: {result.stderr.strip()}")
    return result.stdout.strip()
