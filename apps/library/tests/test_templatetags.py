from apps.library.templatetags.library_extras import duration_hms


class TestDurationHms:
    def test_none_is_blank(self):
        assert duration_hms(None) == ""

    def test_seconds_under_a_minute(self):
        assert duration_hms(45) == "0:45"

    def test_minutes_and_seconds(self):
        assert duration_hms(225) == "3:45"

    def test_pads_single_digit_seconds(self):
        assert duration_hms(185) == "3:05"

    def test_rounds_fractional_seconds(self):
        assert duration_hms(224.6) == "3:45"

    def test_includes_hours_once_an_hour_is_reached(self):
        assert duration_hms(3725) == "1:02:05"

    def test_negative_is_blank(self):
        assert duration_hms(-5) == ""

    def test_non_numeric_is_blank(self):
        assert duration_hms("not a number") == ""
