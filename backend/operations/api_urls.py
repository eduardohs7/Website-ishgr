from django.urls import path

from . import api

app_name = "management_api"
urlpatterns = [
    path("session/", api.session, name="session"),
    path("registrations/", api.registrations, name="registrations"),
    path("registrations/<str:registration_id>/", api.registration, name="registration"),
    path("registrations/<str:registration_id>/review/", api.review, name="review"),
    path("registrations/<str:registration_id>/cancel/", api.cancel, name="cancel"),
    path("indicators/", api.indicators, name="indicators"),
]
