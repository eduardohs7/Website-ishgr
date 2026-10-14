from django.urls import path

from . import api, security_api

app_name = "auth_api"
urlpatterns = [
    path("session/", api.session, name="session"),
    path("signup/", api.signup, name="signup"),
    path("login/", api.login, name="login"),
    path("logout/", api.logout, name="logout"),
    path("change-password/", security_api.change_password, name="change_password"),
    path("logout-all/", security_api.logout_all, name="logout_all"),
    path("confirm-email/", api.confirm_email, name="confirm"),
    path("resend-confirmation/", api.email_request, {"purpose": "confirm"}, name="resend_confirmation"),
    path("request-password-reset/", api.email_request, {"purpose": "reset"}, name="request_reset"),
    path("reset-password/", api.reset_password, name="reset"),
]
