from django import template

register = template.Library()


@register.filter
def duration_hms(seconds):
    """Format a TrackFile.duration (seconds, possibly fractional) as a clock string.

    MM:SS for anything under an hour, H:MM:SS once it reaches one — the
    same convention audio players generally use, so a typical ~3-minute
    track reads "3:45" rather than the constant, mostly-zero "00:03:45"
    a fixed-width HH:MM:SS would print for every single track.
    """
    if seconds is None:
        return ""
    try:
        # round() with no ndigits already returns an int, not another float.
        total_seconds = round(float(seconds))
    except (TypeError, ValueError):
        return ""
    if total_seconds < 0:
        return ""

    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
