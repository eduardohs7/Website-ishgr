from django.urls import path

from registrations import api as registration_api
from . import participant_api

app_name = "participant_api"
urlpatterns = [
    path("profile/", participant_api.profile, name="profile"),
    path("profile/options/", participant_api.profile_options, name="profile_options"),
    path("event/", registration_api.event, name="event"),
    path("prices/", registration_api.prices, name="prices"),
    path("registration/", registration_api.registration, name="registration"),
]
