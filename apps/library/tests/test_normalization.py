from apps.library.normalization import normalize_text


def test_normalize_lowercases():
    assert normalize_text("The Beatles") == "the beatles"


def test_normalize_strips_accents():
    assert normalize_text("Beyoncé") == "beyonce"


def test_normalize_strips_punctuation():
    assert normalize_text("Rock & Roll!") == "rock roll"


def test_normalize_collapses_whitespace():
    assert normalize_text("Too   Many   Spaces") == "too many spaces"


def test_normalize_empty_string():
    assert normalize_text("") == ""
    assert normalize_text(None) == ""


def test_normalize_is_used_for_matching_not_display():
    a = normalize_text("Café del Mar")
    b = normalize_text("cafe DEL MAR")
    assert a == b
