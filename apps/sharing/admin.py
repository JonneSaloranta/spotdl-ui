from django.contrib import admin
from django.shortcuts import render
from django.urls import path, reverse

from apps.core.audit import log_event
from apps.sharing.forms import CreateSharedLinkForm
from apps.sharing.models import SharedImportLink


@admin.register(SharedImportLink)
class SharedImportLinkAdmin(admin.ModelAdmin):
    list_display = (
        "label", "created_by", "enabled", "expires_at", "uses", "maximum_uses",
        "max_items_per_submission", "created_at",
    )
    list_filter = ("enabled",)
    readonly_fields = ("token_hash", "created_by", "created_at", "uses")
    change_list_template = "admin/sharing/sharedimportlink/change_list.html"

    def has_add_permission(self, request):
        # New links are created through create_link below instead of the
        # stock add form: it needs to generate the raw token and show it
        # exactly once, which a plain ModelForm can't do (token_hash is a
        # hash with no form field of its own — see SharedImportLink docstring).
        return False

    def has_change_permission(self, request, obj=None):
        # Any staff member who can reach the admin at all may manage
        # shared links (enable/disable, adjust limits) without needing a
        # separate, granular Django permission grant, matching CLAUDE.md
        # #11's "admins manage shared-link defaults" at the is_staff
        # level rather than a per-permission one.
        return True

    def get_urls(self):
        custom_urls = [
            path("create/", self.admin_site.admin_view(self.create_link), name="sharing_sharedimportlink_create"),
        ]
        return custom_urls + super().get_urls()

    def create_link(self, request):
        """Admin-only "create an invite link" page (CLAUDE.md #10).

        GET shows the form; a successful POST shows the generated URL and
        raw token exactly once — this is the only time either is ever
        retrievable, so the confirmation page makes that explicit.
        """
        if request.method == "POST":
            form = CreateSharedLinkForm(request.POST)
            if form.is_valid():
                link, raw_token = SharedImportLink.generate(
                    created_by=request.user,
                    label=form.cleaned_data["label"],
                    ttl_hours=form.cleaned_data["ttl_hours"],
                    maximum_uses=form.cleaned_data["maximum_uses"],
                    max_items_per_submission=form.cleaned_data["max_items_per_submission"],
                )
                log_event(
                    "shared_link_created", request=request, target=link,
                    detail={"ttl_hours": form.cleaned_data["ttl_hours"], "maximum_uses": link.maximum_uses},
                )
                share_url = request.build_absolute_uri(reverse("sharing:submit", args=[raw_token]))
                return render(request, "admin/sharing/sharedimportlink/created.html", {
                    **self.admin_site.each_context(request),
                    "opts": self.model._meta,
                    "link": link,
                    "share_url": share_url,
                    "title": "Shared link created",
                })
        else:
            form = CreateSharedLinkForm()

        return render(request, "admin/sharing/sharedimportlink/create.html", {
            **self.admin_site.each_context(request),
            "opts": self.model._meta,
            "form": form,
            "title": "Create shared import link",
        })
