# spotDL command syntax verification

CLAUDE.md #4 requires that spotDL's command syntax be verified against the
installed version rather than guessed. This document records exactly what
was and was not verified, and against which version, so a future version
bump knows what to re-check.

## Verified (spotdl==4.5.2)

Run directly in the development environment on 2026-08-19:

```
spotdl --help
spotdl download --help
spotdl save --help
spotdl --version   # -> 4.5.2
```

Confirmed from real `--help` output (not documentation/guesswork):

- Operation is the first positional argument: `download`, `save`, `sync`, `meta`, `url`, `web`.
- `save` requires `--save-file PATH` and writes a JSON array of song objects — used by
  `apps.downloader.services.spotdl.resolve_source()` to discover tracks without downloading audio.
- `download` accepts `--output TEMPLATE`, `--format {mp3,flac,ogg,opus,m4a,wav}`,
  `--overwrite {skip,force,metadata}`, `--bitrate ...`, `--threads N`, `--max-retries N`,
  `--print-errors`, `--client-id`/`--client-secret`, `--headless`, `--log-level`.
- All flags used by `apps/downloader/services/spotdl.py` (`build_resolve_command`,
  `build_download_command`, `_base_args`) match this output exactly.

This is the basis for `apps.downloader.services.spotdl.check_installed_version()`, which
admins can use to detect drift between `SPOTDL_VERSION` and the actually-installed binary.

## Update: JSON schema verified against a live response (2026-08-19)

The sandbox this document was originally written in had no route to Spotify's API, so the
paragraph below was left as an open risk. Once the app was running for real inside
`docker compose` (which has normal outbound network access), a real end-to-end submission
was made — `https://open.spotify.com/track/7GhIk7Il098yCjg4BQjzvb` — and
`resolve_batch_task` correctly resolved and stored:

```
title:  Never Gonna Give You Up
artist: Rick Astley
album:  Whenever You Need Somebody
source_identifier: 7GhIk7Il098yCjg4BQjzvb
```

This confirms `_parse_song()`'s field names (`song_id`, `url`, `name`, `artist`, `album_name`,
etc.) match spotDL 4.5.2's real `.spotdl` save-file schema, not just the public `Song`
dataclass documentation. The task then proceeded into `download_item_task` and began running
the real `spotdl download` subprocess. The original "not verified" caveat below is kept for
history and because a future spotDL version could still change the schema — re-check it on
any version bump per the checklist at the end of this document.

## Originally unverified (now confirmed above, kept for context)

The **JSON schema of `.spotdl` save files** (the field names `_parse_song()` reads —
`song_id`, `url`, `name`, `artist`/`artists`, `album_name`, `album_artist`, `duration`,
`track_number`, `disc_number`, `isrc`, `list_name`, `list_position`) was originally documented
from spotDL's public `Song` dataclass without having exercised it against a real Spotify
response, because outbound access to Spotify's API was not reachable from the sandbox this was
first developed in (`spotdl save <url> ...` timed out after 25s with no network path to
Spotify there). `_parse_song()` is written defensively regardless — every field is read with
`.get()` and degrades to an empty/`None` value rather than raising — so an unverified or
drifted schema would have degraded gracefully instead of crashing a whole batch even before
the live confirmation above.

## Update: `--simple-tui` stage output verified against a live download (2026-08-19)

Live download progress (the progress bar on the batch page) was previously stuck at 10% for
the whole download and then jumped straight to 80%/100%, because `download_track()` only ever
saw output once the whole subprocess had already exited. Fixing that needed to know exactly
what spotDL prints *while downloading*, which wasn't previously verified either — done now by
running a real download directly in the `worker` container and capturing raw stdout
(`docker compose exec worker spotdl ... --simple-tui download <url> ...`):

```
Processing query: https://open.spotify.com/track/0gmL4YsYnz0e3OYqJwsBlJ
YKSIN - SINISTERTEKK - SLOWED: Searching for song
YKSIN - SINISTERTEKK - SLOWED: Getting audio meta
YKSIN - SINISTERTEKK - SLOWED: Downloading
YKSIN - SINISTERTEKK - SLOWED: Embedding metadata
YKSIN - SINISTERTEKK - SLOWED: Done
1/1 complete
Downloaded "YKSIN - SINISTERTEKK - SLOWED":
https://www.youtube.com/watch?v=MoTlbx5D5XA
```

Without `--simple-tui` (spotDL's default), the same run printed only the first and last of
those lines — the in-between stages are drawn as an updating terminal progress bar (carriage
returns, not newlines), which a captured subprocess pipe can't usefully parse. `--simple-tui`
was added to `_base_args()` specifically so `_detect_stage_progress()` has one plain line per
stage to match against; see `apps/downloader/services/spotdl.py` for the percentage mapping and
`_run()`'s `select()`-based incremental read loop that lets `on_progress` fire as each line
arrives rather than only once the process exits. Not verified: whether this exact wording is
stable across spotDL versions — a rename degrades silently to "no live update for that one
stage" (see `_detect_stage_progress()`'s docstring), never an error, so re-check this section on
a version bump but treat a mismatch as low-severity if missed.

## Update: "no results found" error message verified against a real failed match (2026-08-19)

Reported for real: a failed track's error message showed as "found for song: Quentin Noire -
Skyline Sonnet" — missing "No results" from the front. Reproduced directly against the installed
binary with a query guaranteed not to match anything:

```
$ spotdl --headless --simple-tui download "Quentin Noire - Skyline Sonnet" ...
Processing query: Quentin Noire - Skyline Sonnet
Quentin Noire - Skyline Sonnet: Searching for song
1/1 complete
LookupError: No results found for song: Quentin Noire - Skyline Sonnet
https://open.spotify.com/track/5IqPPFogK8Quw9iKgnvqxl - LookupError: No results
found for song: Quentin Noire - Skyline Sonnet
```

spotDL prints the message twice: once on its own, unwrapped, and again folded into a longer
`<url> - LookupError: message` summary line that its own console output (the `rich` library)
word-wraps across the terminal width once the url pushes it over 80-ish columns — both on
stdout, real `\n`-terminated lines (confirmed with `cat -A`), stderr empty, exit code 0 even on a
failed match. `_extract_error_message()` (`apps/downloader/services/spotdl.py`) took "the last
non-blank line" unconditionally, which for this output is only the tail fragment after the wrap.
Fixed by preferring a line matching spotDL's own `XxxError: message` exception-repr shape over
the positional last-line heuristic — see `_extract_error_message()`'s docstring.

## Re-verification checklist for a future spotDL version bump

1. `pip install spotdl==<new-version>` in a scratch venv.
2. Re-run the three `--help` commands above; diff against this document.
3. Run a real `spotdl save` against a live track and diff the JSON keys against `_parse_song()`.
4. Run a real `spotdl ... --simple-tui download <url> ...` and diff the stage wording against
   `apps.downloader.services.spotdl._STAGE_PROGRESS`.
5. Update `SPOTDL_VERSION` in `.env.example` and the pin in `requirements/base.txt`.
6. Update this document's "Verified" section with the new version and date.
