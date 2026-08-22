from django import forms
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from apps.downloader.validators import UnsafeURLError, validate_source_urls


class SharedSubmitForm(forms.Form):
    """A shared-link guest's own submission form — same shape as the
    authenticated one (apps.downloader.forms), but the max URL count comes
    from the SharedImportLink itself, not the site default."""

    urls = forms.CharField(
        label=_("URLs"),
        widget=forms.Textarea(attrs={
            "rows": 4,
            # Not translated: it's a literal example URL, not language-dependent text.
            "placeholder": "https://open.spotify.com/track/...\nhttps://www.youtube.com/watch?v=...",
            "class": "form-control",
        }),
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


class CreateSharedLinkForm(forms.Form):
    """Staff-only: create an invite link (apps.sharing.admin.SharedImportLinkAdmin.create_link)."""

    label = forms.CharField(
        label=_("Label"), required=False, max_length=100,
        help_text=_("Optional — shown to the guest and in the admin list, e.g. “Jane's invite”."),
        widget=forms.TextInput(attrs={"class": "vTextField"}),
    )
    ttl_hours = forms.IntegerField(label=_("Expires after (hours)"), min_value=1)
    maximum_uses = forms.IntegerField(label=_("Maximum uses"), min_value=1)
    max_items_per_submission = forms.IntegerField(label=_("Maximum URLs per submission"), min_value=1)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["ttl_hours"].initial = settings.SHARED_LINK_DEFAULT_TTL_HOURS
        self.fields["ttl_hours"].help_text = _(
            "Site maximum: %(max)d hours."
        ) % {"max": settings.SHARED_LINK_MAX_TTL_HOURS}
        self.fields["maximum_uses"].initial = min(5, settings.SHARED_LINK_MAX_USES)
        self.fields["maximum_uses"].help_text = _(
            "Site maximum: %(max)d. One “use” is one submission of one or more URLs."
        ) % {"max": settings.SHARED_LINK_MAX_USES}
        self.fields["max_items_per_submission"].initial = settings.SHARED_LINK_MAX_ITEMS_PER_SUBMISSION
        self.fields["max_items_per_submission"].help_text = _(
            "Site maximum: %(max)d."
        ) % {"max": settings.SHARED_LINK_MAX_ITEMS_PER_SUBMISSION}

    def clean_ttl_hours(self):
        value = self.cleaned_data["ttl_hours"]
        if value > settings.SHARED_LINK_MAX_TTL_HOURS:
            raise forms.ValidationError(
                _("Cannot exceed the site maximum of %(max)d hours.") % {"max": settings.SHARED_LINK_MAX_TTL_HOURS}
            )
        return value

    def clean_maximum_uses(self):
        value = self.cleaned_data["maximum_uses"]
        if value > settings.SHARED_LINK_MAX_USES:
            raise forms.ValidationError(
                _("Cannot exceed the site maximum of %(max)d.") % {"max": settings.SHARED_LINK_MAX_USES}
            )
        return value

    def clean_max_items_per_submission(self):
        value = self.cleaned_data["max_items_per_submission"]
        if value > settings.SHARED_LINK_MAX_ITEMS_PER_SUBMISSION:
            raise forms.ValidationError(
                _("Cannot exceed the site maximum of %(max)d.")
                % {"max": settings.SHARED_LINK_MAX_ITEMS_PER_SUBMISSION}
            )
        return value
