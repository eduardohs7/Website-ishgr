from django.urls import path

from . import api, audit_api

app_name = "management_api"
urlpatterns = [
    path("session/", api.session, name="session"),
    path("registrations/", api.registrations, name="registrations"),
    path("registrations/export/", api.export, name="export"),
    path("registrations/<str:registration_id>/", api.registration, name="registration"),
    path("registrations/<str:registration_id>/review/", api.review, name="review"),
    path("registrations/<str:registration_id>/cancel/", api.cancel, name="cancel"),
    path("indicators/", api.indicators, name="indicators"),
    path("audit/", audit_api.entries, name="audit_entries"),
    path("audit/<str:entry_id>/", audit_api.entry, name="audit_entry"),
]
