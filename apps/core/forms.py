from django import forms

from apps.core.models import AUDIO_PROVIDER_CHOICES, SiteSettings


class SiteSettingsForm(forms.ModelForm):
    """Backs the simplified staff settings page (CLAUDE.md #11) — a
    lighter-weight alternative to the Django admin change form for the
    handful of fields an operator actually adjusts day-to-day. Deep
    administration (users, shared links, audit log, individual batches)
    stays in Django admin rather than being duplicated here."""

    # Declared explicitly (rather than left to ModelForm's auto-generation)
    # so it renders as checkboxes backed by a real multi-select, while the
    # model itself stores a plain comma-separated CharField — see
    # SiteSettings.fallback_audio_providers_list(). clean_fallback_audio_providers()
    # below converts back to that stored string form.
    fallback_audio_providers = forms.MultipleChoiceField(
        choices=AUDIO_PROVIDER_CHOICES,
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Fallback audio provider(s)",
        help_text="Used once the primary provider's attempts (below) are exhausted.",
    )

    class Meta:
        model = SiteSettings
        fields = [
            "site_name",
            "maintenance_mode",
            "maintenance_message",
            "default_duplicate_policy",
            "auto_replace_probable_duplicates",
            "max_urls_per_batch",
            "musicbrainz_enabled",
            "musicbrainz_sweep_batch_size",
            "primary_audio_provider",
            "primary_provider_attempts",
            "fallback_audio_providers",
        ]
        widgets = {
            "site_name": forms.TextInput(attrs={"class": "form-control"}),
            "maintenance_mode": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "maintenance_message": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
            "default_duplicate_policy": forms.Select(attrs={"class": "form-select"}),
            "auto_replace_probable_duplicates": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "max_urls_per_batch": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "musicbrainz_enabled": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "musicbrainz_sweep_batch_size": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "primary_audio_provider": forms.Select(attrs={"class": "form-select"}),
            "primary_provider_attempts": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["fallback_audio_providers"].initial = self.instance.fallback_audio_providers_list()

    def clean_fallback_audio_providers(self):
        return ",".join(self.cleaned_data["fallback_audio_providers"])
