from django.urls import path

from . import api

app_name = "certificate_management"
urlpatterns = [
    path("", api.drafts, name="drafts"),
    path("prepare/", api.prepare, name="prepare"),
    path("<str:draft_id>/", api.draft, name="draft"),
]
