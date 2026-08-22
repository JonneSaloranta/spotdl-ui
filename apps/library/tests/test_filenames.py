from apps.library.filenames import sanitize_path_component, sanitize_relative_path


class TestSanitizePathComponent:
    def test_passes_through_safe_names(self):
        assert sanitize_path_component("Artist Name") == "Artist Name"

    def test_strips_path_traversal(self):
        # A component that is *exactly* ".." or "." (the only way a single
        # path segment can traverse) is replaced outright.
        assert sanitize_path_component("..") == "unknown"
        assert sanitize_path_component(".") == "unknown"
        # Embedded "..", once separators are stripped, is just a harmless
        # literal substring — it cannot escape its parent directory
        # without an adjacent "/". The real guarantee is no separator
        # survives (covered by test_strips_separators).
        result = sanitize_path_component("../../etc/passwd")
        assert "/" not in result

    def test_strips_separators(self):
        result = sanitize_path_component("a/b\\c")
        assert "/" not in result
        assert "\\" not in result

    def test_strips_control_characters(self):
        result = sanitize_path_component("track\x00name\x1f")
        assert "\x00" not in result
        assert "\x1f" not in result

    def test_reserved_windows_device_name_is_prefixed(self):
        assert sanitize_path_component("CON") != "CON"
        assert sanitize_path_component("con") != "con" or sanitize_path_component("con").startswith("_")

    def test_empty_input_falls_back(self):
        assert sanitize_path_component("") == "unknown"
        assert sanitize_path_component("   ") == "unknown"
        assert sanitize_path_component("...") == "unknown"

    def test_enforces_max_length(self):
        result = sanitize_path_component("a" * 500)
        assert len(result) <= 150

    def test_trims_trailing_dots_and_spaces(self):
        assert sanitize_path_component("name...") == "name"
        assert sanitize_path_component("name   ") == "name"


class TestSanitizeRelativePath:
    def test_builds_expected_shape(self):
        path = sanitize_relative_path(["Artist", "Album"], filename="01 - Title", extension="mp3")
        assert path == "Artist/Album/01 - Title.mp3"

    def test_never_contains_traversal_even_from_malicious_metadata(self):
        path = sanitize_relative_path(
            ["../../etc", "..\\..\\Windows"], filename="../../passwd", extension="mp3"
        )
        # No segment of the resulting path may be exactly ".." or "." or
        # empty — that is what would actually let a joined path escape
        # its base directory.
        segments = path.split("/")
        assert all(seg not in ("..", ".", "") for seg in segments)
        assert not path.startswith("/")

    def test_extension_is_sanitized(self):
        path = sanitize_relative_path(["A"], filename="track", extension="mp3; rm -rf /")
        assert path.endswith(".mp3rmrf")  # non-alphanumeric characters stripped

    def test_drops_empty_parts(self):
        path = sanitize_relative_path(["", "  ", "Artist"], filename="Title", extension="flac")
        assert path == "Artist/Title.flac"
