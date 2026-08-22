import pytest

from apps.downloader.validators import (
    UnsafeURLError,
    validate_source_url,
    validate_source_urls,
)


class TestValidateSourceURL:
    def test_accepts_spotify_track_url(self):
        result = validate_source_url(
            "https://open.spotify.com/track/7GhIk7Il098yCjg4BQjzvb", resolve_dns=False
        )
        assert result.host == "open.spotify.com"
        assert result.normalized.startswith("https://open.spotify.com/track/")

    def test_accepts_youtube_watch_url(self):
        result = validate_source_url(
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ", resolve_dns=False
        )
        assert result.host == "www.youtube.com"

    def test_accepts_youtube_short_url(self):
        result = validate_source_url("https://youtu.be/dQw4w9WgXcQ", resolve_dns=False)
        assert result.host == "youtu.be"

    def test_accepts_youtube_music_url(self):
        result = validate_source_url(
            "https://music.youtube.com/watch?v=dQw4w9WgXcQ", resolve_dns=False
        )
        assert result.host == "music.youtube.com"

    def test_rejects_youtube_lookalike_host(self):
        # A host that merely contains "youtube" must not slip through —
        # only youtube.com and its real subdomains/youtu.be are allowed.
        with pytest.raises(UnsafeURLError):
            validate_source_url("https://youtube.com.evil.example/watch?v=x", resolve_dns=False)

    def test_strips_fragment(self):
        result = validate_source_url(
            "https://open.spotify.com/track/abc123#footer", resolve_dns=False
        )
        assert "#" not in result.normalized

    def test_rejects_http_scheme(self):
        with pytest.raises(UnsafeURLError):
            validate_source_url("http://open.spotify.com/track/abc123", resolve_dns=False)

    def test_rejects_file_scheme(self):
        with pytest.raises(UnsafeURLError):
            validate_source_url("file:///etc/passwd", resolve_dns=False)

    def test_rejects_unsupported_host(self):
        with pytest.raises(UnsafeURLError):
            validate_source_url("https://evil.example.com/track/abc123", resolve_dns=False)

    def test_rejects_embedded_credentials(self):
        with pytest.raises(UnsafeURLError):
            validate_source_url("https://user:pass@open.spotify.com/track/abc", resolve_dns=False)

    def test_rejects_empty_url(self):
        with pytest.raises(UnsafeURLError):
            validate_source_url("", resolve_dns=False)

    def test_rejects_control_characters(self):
        with pytest.raises(UnsafeURLError):
            validate_source_url("https://open.spotify.com/track/abc\x00123", resolve_dns=False)

    def test_rejects_overly_long_url(self):
        with pytest.raises(UnsafeURLError):
            validate_source_url("https://open.spotify.com/" + "a" * 3000, resolve_dns=False)

    def test_rejects_private_ip_target(self):
        # SSRF guard: a hostname resolving to a loopback/private address
        # must be rejected even if it superficially looks like an
        # allowed host would (not applicable here since host allow-list
        # already blocks it, but the DNS check is exercised directly).
        from apps.downloader import validators

        assert validators._is_private_or_reserved("localhost") is True

    def test_accepts_public_hostname_resolution(self):
        from apps.downloader import validators

        # open.spotify.com should resolve to a public address; this is a
        # best-effort network check, skipped gracefully if DNS is
        # unavailable in the test environment.
        try:
            is_private = validators._is_private_or_reserved("open.spotify.com")
        except UnsafeURLError:
            pytest.skip("DNS not available in this environment")
        assert is_private is False


class TestValidateSourceURLs:
    def test_rejects_empty_list(self):
        with pytest.raises(UnsafeURLError):
            validate_source_urls([], max_items=10, resolve_dns=False)

    def test_rejects_too_many_urls(self):
        urls = [f"https://open.spotify.com/track/{i:022d}" for i in range(5)]
        with pytest.raises(UnsafeURLError):
            validate_source_urls(urls, max_items=3, resolve_dns=False)

    def test_accepts_valid_batch(self):
        urls = [
            "https://open.spotify.com/track/abc123",
            "https://open.spotify.com/playlist/def456",
        ]
        result = validate_source_urls(urls, max_items=10, resolve_dns=False)
        assert len(result) == 2
