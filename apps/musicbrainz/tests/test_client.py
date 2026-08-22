from unittest.mock import patch

import pytest
import requests
import responses
from django.core.cache import cache

from apps.musicbrainz.models import MusicBrainzRecording
from apps.musicbrainz.services import MusicBrainzClient, MusicBrainzConnectionError

pytestmark = pytest.mark.django_db

BASE_URL = "https://musicbrainz.example.org/ws/2"


def make_client(**overrides):
    kwargs = dict(
        base_url=BASE_URL, user_agent="spotdl-ui/test", timeout=5, rate_limit_seconds=0,
    )
    kwargs.update(overrides)
    return MusicBrainzClient(**kwargs)


@pytest.fixture(autouse=True)
def clear_cache():
    cache.clear()
    yield
    cache.clear()


SEARCH_RESPONSE = {
    "recordings": [
        {
            "id": "mbid-1",
            "title": "Never Gonna Give You Up",
            "score": 100,
            "length": 213000,
            "artist-credit": [{"name": "Rick Astley", "artist": {"id": "artist-mbid-1"}}],
            "releases": [{"id": "release-mbid-1", "title": "Whenever You Need Somebody"}],
            "isrcs": ["GBARL9300135"],
        }
    ]
}


class TestUserAgent:
    def test_includes_contact_when_configured(self, settings):
        settings.MUSICBRAINZ_CONTACT = "admin@example.com"
        client = MusicBrainzClient(base_url=BASE_URL, user_agent="spotdl-ui/1.0")
        assert client.user_agent == "spotdl-ui/1.0 ( admin@example.com )"

    def test_omits_contact_when_not_configured(self, settings):
        settings.MUSICBRAINZ_CONTACT = ""
        client = MusicBrainzClient(base_url=BASE_URL, user_agent="spotdl-ui/1.0")
        assert client.user_agent == "spotdl-ui/1.0"


class TestSearchRecordings:
    @responses.activate
    def test_parses_candidates(self):
        responses.add(responses.GET, f"{BASE_URL}/recording", json=SEARCH_RESPONSE, status=200)
        client = make_client()
        results = client.search_recordings(title="Never Gonna Give You Up", artist="Rick Astley")
        assert len(results) == 1
        candidate = results[0]
        assert candidate.mbid == "mbid-1"
        assert candidate.artist == "Rick Astley"
        assert candidate.release == "Whenever You Need Somebody"
        assert candidate.length_ms == 213000
        assert candidate.isrcs == ["GBARL9300135"]

    @responses.activate
    def test_sends_descriptive_user_agent(self):
        responses.add(responses.GET, f"{BASE_URL}/recording", json={"recordings": []}, status=200)
        client = make_client(user_agent="spotdl-ui/9.9")
        client.search_recordings(title="X")
        assert responses.calls[0].request.headers["User-Agent"] == "spotdl-ui/9.9"

    @responses.activate
    def test_empty_results(self):
        responses.add(responses.GET, f"{BASE_URL}/recording", json={"recordings": []}, status=200)
        assert make_client().search_recordings(title="Nothing") == []


class TestGetRecording:
    @responses.activate
    def test_fetches_and_caches(self):
        responses.add(
            responses.GET, f"{BASE_URL}/recording/mbid-1",
            json={
                "id": "mbid-1", "title": "T", "length": 200000,
                "artist-credit": [{"name": "A", "artist": {"id": "amb"}}],
                "releases": [{"id": "rmb", "title": "R"}],
                "isrcs": ["ISRC1"],
            },
            status=200,
        )
        client = make_client()
        recording = client.get_recording("mbid-1")
        assert isinstance(recording, MusicBrainzRecording)
        assert recording.title == "T"
        assert MusicBrainzRecording.objects.filter(mbid="mbid-1").exists()

    @responses.activate
    def test_uses_cache_without_a_network_call(self):
        MusicBrainzRecording.objects.create(mbid="mbid-1", title="Cached")
        client = make_client()
        recording = client.get_recording("mbid-1")
        assert recording.title == "Cached"
        assert len(responses.calls) == 0

    @responses.activate
    def test_use_cache_false_forces_refetch(self):
        MusicBrainzRecording.objects.create(mbid="mbid-1", title="Stale")
        responses.add(
            responses.GET, f"{BASE_URL}/recording/mbid-1",
            json={"id": "mbid-1", "title": "Fresh", "artist-credit": [{}], "releases": [{}]},
            status=200,
        )
        recording = make_client().get_recording("mbid-1", use_cache=False)
        assert recording.title == "Fresh"


class TestRetryAndFailure:
    @responses.activate
    def test_retries_then_succeeds(self):
        responses.add(responses.GET, f"{BASE_URL}/recording", body=requests.exceptions.ConnectionError("boom"))
        responses.add(responses.GET, f"{BASE_URL}/recording", json={"recordings": []}, status=200)
        with patch("apps.musicbrainz.services.time.sleep"):
            result = make_client().search_recordings(title="X")
        assert result == []

    @responses.activate
    def test_raises_after_exhausting_retries(self):
        for _ in range(5):
            responses.add(responses.GET, f"{BASE_URL}/recording", body=requests.exceptions.ConnectionError("boom"))
        with patch("apps.musicbrainz.services.time.sleep"):
            with pytest.raises(MusicBrainzConnectionError):
                make_client().search_recordings(title="X")

    @responses.activate
    def test_503_is_treated_as_retryable(self):
        responses.add(responses.GET, f"{BASE_URL}/recording", status=503)
        responses.add(responses.GET, f"{BASE_URL}/recording", json={"recordings": []}, status=200)
        with patch("apps.musicbrainz.services.time.sleep"):
            result = make_client().search_recordings(title="X")
        assert result == []


class TestRateLimiting:
    @responses.activate
    def test_throttles_between_requests(self):
        responses.add(responses.GET, f"{BASE_URL}/recording", json={"recordings": []}, status=200)
        responses.add(responses.GET, f"{BASE_URL}/recording", json={"recordings": []}, status=200)

        client = make_client(rate_limit_seconds=1.0)
        with patch("apps.musicbrainz.services.time.sleep") as mock_sleep:
            client.search_recordings(title="A")
            client.search_recordings(title="B")
        # The second call happens immediately after the first in test time,
        # so the throttle must have slept to respect the 1s spacing.
        assert mock_sleep.called
