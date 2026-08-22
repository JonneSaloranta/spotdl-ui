from django import forms
from django.utils.translation import gettext_lazy as _

from apps.downloader.validators import UnsafeURLError, validate_source_urls


class BatchSubmitForm(forms.Form):
    """One or more source URLs, submitted as one per line (CLAUDE.md #12)."""

    urls = forms.CharField(
        label=_("URLs"),
        widget=forms.Textarea(attrs={
            "rows": 4,
            # Not translated: it's a literal example URL, not language-dependent text.
            "placeholder": "https://open.spotify.com/track/...\nhttps://www.youtube.com/watch?v=...",
            "class": "form-control",
        }),
        help_text=_("One Spotify or YouTube track, album, or playlist URL per line."),
    )

    def __init__(self, *args, max_urls: int = 25, **kwargs):
        self.max_urls = max_urls
        super().__init__(*args, **kwargs)

    def clean_urls(self):
        raw = self.cleaned_data["urls"]
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        try:
            validated = validate_source_urls(lines, max_items=self.max_urls)
        except UnsafeURLError as exc:
            raise forms.ValidationError(exc.message or str(exc)) from exc
        return validated
