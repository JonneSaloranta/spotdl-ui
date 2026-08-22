import hashlib

from apps.library.hashing import sha256_file


def test_sha256_file_matches_stdlib(tmp_path):
    content = b"some fake audio bytes" * 1000
    file_path = tmp_path / "track.mp3"
    file_path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert sha256_file(file_path) == expected


def test_sha256_file_is_stable_across_chunk_boundary(tmp_path):
    # Content larger than the 1 MiB internal chunk size, to exercise the
    # streaming loop rather than a single read().
    content = b"x" * (1024 * 1024 + 12345)
    file_path = tmp_path / "big.wav"
    file_path.write_bytes(content)

    expected = hashlib.sha256(content).hexdigest()
    assert sha256_file(file_path) == expected


def test_sha256_file_differs_for_different_content(tmp_path):
    a = tmp_path / "a.mp3"
    b = tmp_path / "b.mp3"
    a.write_bytes(b"content a")
    b.write_bytes(b"content b")
    assert sha256_file(a) != sha256_file(b)
