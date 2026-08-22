from pathlib import Path

import pytest

from apps.downloader.services import spotdl


class TestBuildCommands:
    def test_resolve_command_uses_save_operation(self):
        args = spotdl.build_resolve_command(
            "https://open.spotify.com/track/abc", Path("/tmp/x.spotdl")
        )
        assert args[0] == "spotdl"
        assert "save" in args
        assert "https://open.spotify.com/track/abc" in args
        assert "--save-file" in args
        assert str(Path("/tmp/x.spotdl")) in args
        # never invoked through a shell
        assert isinstance(args, list)

    def test_download_command_uses_skip_overwrite(self):
        args = spotdl.build_download_command("https://open.spotify.com/track/abc", Path("/data/out"))
        assert "download" in args
        assert "--overwrite" in args
        assert args[args.index("--overwrite") + 1] == "skip"

    def test_download_command_output_template_is_scoped_to_output_dir(self):
        args = spotdl.build_download_command("https://open.spotify.com/track/abc", Path("/data/out"))
        output = args[args.index("--output") + 1]
        assert output.startswith(str(Path("/data/out")))

    def test_no_argument_ever_contains_shell_metacharacters_unescaped(self):
        # Regression guard: even a maliciously crafted "URL" must only ever
        # appear as a single argv element, never concatenated into a
        # string that could be shell-interpreted (CLAUDE.md #2/#3).
        malicious = "https://open.spotify.com/track/abc; rm -rf /"
        args = spotdl.build_download_command(malicious, Path("/data/out"))
        assert malicious in args
        assert args.count(malicious) == 1

    def test_bitrate_included_when_configured(self, settings):
        settings.SPOTDL_BITRATE = "128k"
        args = spotdl.build_download_command("https://open.spotify.com/track/abc", Path("/data/out"))
        assert "--bitrate" in args
        assert args[args.index("--bitrate") + 1] == "128k"

    def test_bitrate_omitted_when_empty(self, settings):
        settings.SPOTDL_BITRATE = ""
        args = spotdl.build_download_command("https://open.spotify.com/track/abc", Path("/data/out"))
        assert "--bitrate" not in args

    def test_bitrate_disable_is_passed_through_literally(self, settings):
        # spotDL's own documented way to remove the bitrate constraint
        # entirely (keeps the source's original bitrate) — distinct from
        # omitting the flag, which falls back to spotDL's own default.
        settings.SPOTDL_BITRATE = "disable"
        args = spotdl.build_download_command("https://open.spotify.com/track/abc", Path("/data/out"))
        assert args[args.index("--bitrate") + 1] == "disable"

    def test_proxy_omitted_by_default(self, settings):
        settings.SPOTDL_PROXY = ""
        args = spotdl.build_download_command("https://open.spotify.com/track/abc", Path("/data/out"))
        assert "--proxy" not in args

    def test_proxy_included_when_configured(self, settings):
        settings.SPOTDL_PROXY = "socks5://127.0.0.1:1080"
        args = spotdl.build_download_command("https://open.spotify.com/track/abc", Path("/data/out"))
        assert "--proxy" in args
        assert args[args.index("--proxy") + 1] == "socks5://127.0.0.1:1080"

    def test_audio_provider_omitted_when_nothing_configured(self, settings):
        settings.SPOTDL_PRIMARY_AUDIO_PROVIDER = ""
        args = spotdl.build_download_command("https://open.spotify.com/track/abc", Path("/data/out"))
        assert "--audio" not in args

    def test_audio_provider_defaults_to_the_configured_primary(self, settings):
        settings.SPOTDL_PRIMARY_AUDIO_PROVIDER = "youtube-music"
        args = spotdl.build_download_command("https://open.spotify.com/track/abc", Path("/data/out"))
        assert "--audio" in args
        assert args[args.index("--audio") + 1] == "youtube-music"

    def test_explicit_audio_providers_override_the_configured_default(self, settings):
        settings.SPOTDL_PRIMARY_AUDIO_PROVIDER = "youtube-music"
        args = spotdl.build_download_command(
            "https://open.spotify.com/track/abc", Path("/data/out"), audio_providers=["soundcloud"],
        )
        assert "--audio" in args
        idx = args.index("--audio")
        assert args[idx + 1] == "soundcloud"
        assert "youtube-music" not in args

    def test_explicit_empty_audio_providers_list_means_no_audio_flag(self, settings):
        # Distinct from "not passed at all" (which falls back to the
        # configured default) — an empty list is an explicit "use spotDL's
        # own default for this attempt".
        settings.SPOTDL_PRIMARY_AUDIO_PROVIDER = "youtube-music"
        args = spotdl.build_download_command(
            "https://open.spotify.com/track/abc", Path("/data/out"), audio_providers=[],
        )
        assert "--audio" not in args

    def test_audio_flag_comes_after_the_operation_argument(self, settings):
        # Regression guard for a real bug: --audio takes a variable number
        # of values (nargs='*' — `--audio [{youtube,...} ...]` per `spotdl
        # --help`), so placed *before* the positional "download"/"save"
        # argument, argparse greedily swallows that positional as an
        # extra (invalid) provider choice: observed for real as
        # `spotdl: error: argument --audio: invalid choice: 'save'`.
        # --audio must always come after every positional argument.
        download_args = spotdl.build_download_command(
            "https://open.spotify.com/track/abc", Path("/data/out"), audio_providers=["soundcloud"],
        )
        assert download_args.index("--audio") > download_args.index("download")

    def test_proxy_flag_comes_after_the_operation_argument(self, settings):
        settings.SPOTDL_PROXY = "socks5://127.0.0.1:1080"

        download_args = spotdl.build_download_command("https://open.spotify.com/track/abc", Path("/data/out"))
        assert download_args.index("--proxy") > download_args.index("download")

        save_file = Path("/tmp/x.spotdl")
        resolve_args = spotdl.build_resolve_command("https://open.spotify.com/track/abc", save_file)
        assert resolve_args.index("--proxy") > resolve_args.index("save")


class TestParseSong:
    def test_parses_known_fields(self):
        entry = {
            "song_id": "abc123",
            "url": "https://open.spotify.com/track/abc123",
            "name": "Song Title",
            "artist": "Artist Name",
            "album_name": "Album Name",
            "album_artist": "Album Artist",
            "duration": 210.5,
            "track_number": 3,
            "disc_number": 1,
            "isrc": "US1234567890",
            "list_name": "My Playlist",
            "list_position": 5,
        }
        track = spotdl._parse_song(entry, fallback_source_url="https://fallback")
        assert track.source_identifier == "abc123"
        assert track.title == "Song Title"
        assert track.artist == "Artist Name"
        assert track.duration_seconds == 210.5
        assert track.track_number == 3
        assert track.playlist_position == 5

    def test_missing_fields_degrade_gracefully(self):
        # A future spotDL version changing its JSON schema must not crash
        # resolution — every field is optional (CLAUDE.md #4).
        track = spotdl._parse_song({}, fallback_source_url="https://fallback")
        assert track.source_identifier == ""
        assert track.source_url == "https://fallback"
        assert track.title == ""
        assert track.duration_seconds is None
        assert track.track_number is None

    def test_falls_back_to_artists_list(self):
        entry = {"artists": ["A", "B"]}
        track = spotdl._parse_song(entry, fallback_source_url="https://fallback")
        assert track.artist == "A, B"

    def test_malformed_numeric_fields_do_not_raise(self):
        entry = {"duration": "not-a-number", "track_number": "also-not-a-number"}
        track = spotdl._parse_song(entry, fallback_source_url="https://fallback")
        assert track.duration_seconds is None
        assert track.track_number is None


class TestDetectStageProgress:
    def test_recognizes_each_known_stage(self):
        assert spotdl._detect_stage_progress("My Song: Searching for song") == (20, "Searching for song")
        assert spotdl._detect_stage_progress("My Song: Getting audio meta") == (35, "Getting audio meta")
        assert spotdl._detect_stage_progress("My Song: Downloading") == (50, "Downloading")
        assert spotdl._detect_stage_progress("My Song: Embedding metadata") == (70, "Embedding metadata")

    def test_unrecognized_lines_return_none(self):
        assert spotdl._detect_stage_progress("Processing query: https://open.spotify.com/track/x") is None
        assert spotdl._detect_stage_progress("1/1 complete") is None
        assert spotdl._detect_stage_progress("My Song: Done") is None
        assert spotdl._detect_stage_progress("") is None

    def test_stages_are_ordered_and_stay_below_the_post_return_bump(self):
        # tasks.py sets progress=80 itself right after a successful
        # download_track() call returns, so nothing here should reach or
        # exceed that — these only need to cover the gap between the 10%
        # set before the call and that 80%.
        percents = [percent for _, percent in spotdl._STAGE_PROGRESS]
        assert percents == sorted(percents)
        assert all(10 < p < 80 for p in percents)


class TestRunProcessControl:
    def test_run_raises_on_missing_executable(self, settings):
        settings.SPOTDL_EXECUTABLE = "/nonexistent/spotdl-binary"
        with pytest.raises(spotdl.SpotDLError):
            spotdl._run(["/nonexistent/spotdl-binary", "--version"], timeout=5)

    def test_run_respects_cancellation(self):
        # A long-running "sleep" stands in for spotDL; should_cancel flips
        # true on the first poll, so the process must be terminated
        # promptly rather than left to run for the full timeout.
        calls = {"n": 0}

        def should_cancel():
            calls["n"] += 1
            return calls["n"] >= 1

        result = spotdl._run(["sleep", "30"], timeout=60, should_cancel=should_cancel)
        assert result.cancelled is True

    def test_run_captures_timeout(self):
        result = spotdl._run(["sleep", "5"], timeout=0.2)
        assert result.timed_out is True

    def test_captures_stdout_and_stderr(self):
        result = spotdl._run(
            ["python3", "-c", "import sys; print('hello stdout'); print('hello stderr', file=sys.stderr)"],
            timeout=10,
        )
        assert result.exit_code == 0
        assert "hello stdout" in result.stdout
        assert "hello stderr" in result.stderr

    def test_on_progress_called_for_each_recognized_stage_in_order(self):
        # Stands in for spotDL's own --simple-tui output, read as it's
        # produced rather than only once the process exits — this is the
        # actual live-progress mechanism, not just the stage-detection
        # logic tested in isolation above.
        script = (
            "import time\n"
            "print('My Song: Searching for song', flush=True); time.sleep(0.05)\n"
            "print('My Song: Getting audio meta', flush=True); time.sleep(0.05)\n"
            "print('My Song: Downloading', flush=True); time.sleep(0.05)\n"
            "print('My Song: Embedding metadata', flush=True)\n"
            "print('My Song: Done', flush=True)\n"
        )
        seen = []
        result = spotdl._run(
            ["python3", "-c", script], timeout=10,
            on_progress=lambda percent, stage: seen.append((percent, stage)),
        )
        assert result.exit_code == 0
        assert seen == [
            (20, "Searching for song"),
            (35, "Getting audio meta"),
            (50, "Downloading"),
            (70, "Embedding metadata"),
        ]

    def test_on_progress_fires_at_most_once_per_stage(self):
        # A repeated line (e.g. spotDL re-announcing a stage on its own
        # internal retry) must not move progress backwards or spam
        # repeated DB writes from the tasks.py caller.
        script = "print('My Song: Downloading', flush=True)\nprint('My Song: Downloading', flush=True)\n"
        seen = []
        spotdl._run(["python3", "-c", script], timeout=10, on_progress=lambda p, s: seen.append((p, s)))
        assert seen == [(50, "Downloading")]

    def test_omitting_on_progress_still_captures_output_normally(self):
        result = spotdl._run(["python3", "-c", "print('ok')"], timeout=10)
        assert result.exit_code == 0
        assert "ok" in result.stdout

    def test_on_line_called_for_every_line_not_just_recognized_stages(self):
        # Unlike on_progress, on_line must fire for lines _detect_stage_progress()
        # doesn't recognize at all — resolving a source has no stage markers to
        # match against, so this is the only way to see spotDL's own output live
        # (see resolve_source()'s use of it).
        script = (
            "print('Processing query: https://example.com/playlist/x', flush=True)\n"
            "print('Found 2 songs in My Playlist', flush=True)\n"
            "print('Saved 2 songs to out.spotdl', flush=True)\n"
        )
        seen = []
        result = spotdl._run(["python3", "-c", script], timeout=10, on_line=seen.append)
        assert result.exit_code == 0
        assert seen == [
            "Processing query: https://example.com/playlist/x",
            "Found 2 songs in My Playlist",
            "Saved 2 songs to out.spotdl",
        ]

    def test_on_line_skips_blank_lines(self):
        seen = []
        spotdl._run(["python3", "-c", "print('one', flush=True); print(); print('two', flush=True)"],
                     timeout=10, on_line=seen.append)
        assert seen == ["one", "two"]

    def test_heartbeat_logs_while_a_long_running_process_is_still_active(self, monkeypatch, caplog):
        monkeypatch.setattr(spotdl, "_HEARTBEAT_INTERVAL", 0.1)
        with caplog.at_level("INFO", logger="apps.downloader.services.spotdl"):
            spotdl._run(["sleep", "0.5"], timeout=10)
        assert "still running" in caplog.text


class TestExtractErrorMessage:
    def test_prefers_the_unwrapped_exception_line_over_a_wrapped_summary_line(self):
        # Reproduced for real against spotdl==4.5.2: a "no results" error
        # is printed twice — once on its own, unwrapped, and again as
        # part of a longer "<url> - LookupError: message" line that
        # spotDL's own console output (rich) word-wraps across the
        # terminal width once the url pushes it over. Naively taking
        # "the last line" only picks up the tail fragment after the
        # wrap, silently dropping "No results" from the reported error.
        stdout = (
            "Processing query: Quentin Noire - Skyline Sonnet\n"
            "Quentin Noire - Skyline Sonnet: Searching for song\n"
            "1/1 complete\n"
            "LookupError: No results found for song: Quentin Noire - Skyline Sonnet\n"
            "https://open.spotify.com/track/5IqPPFogK8Quw9iKgnvqxl - LookupError: No results\n"
            "found for song: Quentin Noire - Skyline Sonnet\n"
        )
        result = spotdl.ProcessRunResult(exit_code=0, stdout=stdout, stderr="")

        message = spotdl._extract_error_message(result)

        assert message == "LookupError: No results found for song: Quentin Noire - Skyline Sonnet"

    def test_falls_back_to_the_last_line_when_no_exception_line_is_present(self):
        result = spotdl.ProcessRunResult(exit_code=1, stdout="some other output\nfinal diagnostic line\n", stderr="")

        assert spotdl._extract_error_message(result) == "final diagnostic line"

    def test_prefers_stderr_over_stdout(self):
        result = spotdl.ProcessRunResult(
            exit_code=1,
            stdout="AttributeError: wrong one\n",
            stderr="ValueError: the real error\n",
        )

        assert spotdl._extract_error_message(result) == "ValueError: the real error"

    def test_empty_output_returns_empty_string(self):
        result = spotdl.ProcessRunResult(exit_code=1, stdout="", stderr="")
        assert spotdl._extract_error_message(result) == ""

    def test_long_line_is_truncated_to_500_chars(self):
        long_message = "RuntimeError: " + ("x" * 600)
        result = spotdl.ProcessRunResult(exit_code=1, stdout=long_message + "\n", stderr="")

        message = spotdl._extract_error_message(result)

        assert len(message) == 500
        assert message == long_message[:500]
