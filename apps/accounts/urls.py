from django.contrib.auth import views as auth_views
from django.urls import path, reverse_lazy

from apps.accounts import views
from apps.accounts.forms import (
    BootstrapAuthenticationForm,
    BootstrapPasswordResetForm,
    BootstrapSetPasswordForm,
)

app_name = "accounts"

urlpatterns = [
    path(
        "login/",
        views.RateLimitedLoginView.as_view(
            template_name="accounts/login.html", authentication_form=BootstrapAuthenticationForm,
        ),
        name="login",
    ),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path(
        "password-reset/",
        views.RateLimitedPasswordResetView.as_view(
            template_name="accounts/password_reset.html",
            email_template_name="accounts/email/password_reset_email.txt",
            subject_template_name="accounts/email/password_reset_subject.txt",
            form_class=BootstrapPasswordResetForm,
            # Django's default success_url is reverse_lazy("password_reset_done")
            # (unnamespaced) — wrong here since these URLs are namespaced
            # under "accounts:". Without this override, a real password
            # reset submission 500s instead of redirecting.
            success_url=reverse_lazy("accounts:password_reset_done"),
        ),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(template_name="accounts/password_reset_done.html"),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html", form_class=BootstrapSetPasswordForm,
            # Same namespacing issue as RateLimitedPasswordResetView above.
            success_url=reverse_lazy("accounts:password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(template_name="accounts/password_reset_complete.html"),
        name="password_reset_complete",
    ),
    path("theme/", views.set_theme, name="set_theme"),
    path("profile/", views.profile, name="profile"),
]
