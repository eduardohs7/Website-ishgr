from django.urls import path
from . import mfa_api

app_name = "staff_mfa"
urlpatterns = [
    path("status/", mfa_api.status, name="status"),
    path("enroll/", mfa_api.enroll, name="enroll"),
    path("confirm/", mfa_api.confirm, name="confirm"),
    path("verify/", mfa_api.verify, name="verify"),
    path("recovery-codes/", mfa_api.recovery_codes, name="recovery_codes"),
    path("disable/", mfa_api.disable, name="disable"),
]
