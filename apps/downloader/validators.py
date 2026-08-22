"""Source URL validation.

spotDL accepts Spotify URLs (and a few other providers) as download
sources. Because the server resolves and fetches whatever URL a user
submits, this is the project's primary SSRF surface (CLAUDE.md #4,
docs/SECURITY.md) and must not simply trust user input.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

# Hosts that spotDL itself is willing to resolve as a *source* (not audio
# provider — that's a separate, server-side setting, SPOTDL_PRIMARY_AUDIO_PROVIDER).
# spotDL's own --help documents its `query` argument as accepting a
# "Spotify/YouTube URL for a song/playlist/album/artist" — verified
# directly (`spotdl save <youtube-url> ...` resolves real metadata without
# going through Spotify at all) rather than assumed. Keep this narrow:
# widening it widens the SSRF surface (CLAUDE.md #4).
ALLOWED_SOURCE_HOSTS = {
    "open.spotify.com",
    "spotify.link",
    "spotify.com",
    "youtube.com",
    "music.youtube.com",
    "youtu.be",
}

ALLOWED_SCHEMES = {"https"}


class UnsafeURLError(ValidationError):
    """Raised when a submitted URL is rejected for security reasons."""


@dataclass(frozen=True)
class ValidatedSourceURL:
    raw: str
    normalized: str
    host: str


def _is_private_or_reserved(host: str) -> bool:
    """True if `host` resolves to a non-public address (SSRF guard).

    Rejects loopback, link-local, private, and reserved ranges for every
    resolved address of the hostname, so DNS rebinding to an internal
    service is blocked rather than only the literal hostname.
    """
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise UnsafeURLError(_("The URL host could not be resolved.")) from exc

    for family, _type, _proto, _canon, sockaddr in infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            return True  # unparseable address: fail closed
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return True
    return False


def validate_source_url(raw_url: str, *, resolve_dns: bool = True) -> ValidatedSourceURL:
    """Validate a single user-submitted source URL.

    Raises UnsafeURLError (a ValidationError subclass) with a translatable
    message on any rejection. `resolve_dns` is only disabled in unit tests
    that do not have network access.
    """
    raw_url = (raw_url or "").strip()
    if not raw_url:
        raise UnsafeURLError(_("A URL is required."))
    if len(raw_url) > 2048:
        raise UnsafeURLError(_("The URL is too long."))
    if any(ord(ch) < 0x20 for ch in raw_url):
        raise UnsafeURLError(_("The URL contains control characters."))

    parts = urlsplit(raw_url)

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise UnsafeURLError(_("Only https:// URLs are supported."))

    if parts.username or parts.password:
        raise UnsafeURLError(_("URLs with embedded credentials are not allowed."))

    host = (parts.hostname or "").lower()
    if not host:
        raise UnsafeURLError(_("The URL is missing a host."))

    if host != "spotify.com" and not any(
        host == allowed or host.endswith(f".{allowed}") for allowed in ALLOWED_SOURCE_HOSTS
    ):
        raise UnsafeURLError(_("This URL host is not a supported music source."))

    if resolve_dns and _is_private_or_reserved(host):
        raise UnsafeURLError(_("This URL resolves to a disallowed network address."))

    normalized = parts._replace(fragment="").geturl()
    return ValidatedSourceURL(raw=raw_url, normalized=normalized, host=host)


def validate_source_urls(raw_urls: list[str], *, max_items: int, resolve_dns: bool = True) -> list[ValidatedSourceURL]:
    """Validate a batch submission. Raises on the first invalid URL."""
    cleaned = [u for u in (raw_urls or []) if u and u.strip()]
    if not cleaned:
        raise UnsafeURLError(_("At least one URL is required."))
    if len(cleaned) > max_items:
        raise UnsafeURLError(
            _("Too many URLs in one submission (maximum %(max)d).") % {"max": max_items}
        )
    return [validate_source_url(u, resolve_dns=resolve_dns) for u in cleaned]
