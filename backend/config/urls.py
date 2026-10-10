from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

from .views import liveness, readiness
from .portal import public_asset, public_image, public_page

admin.site.site_header = "CHAGS 14 — Administração"
admin.site.site_title = "CHAGS 14"
admin.site.index_title = "Administração"

urlpatterns = [
    path("api/v1/auth/", include("accounts.api_urls")),
    path("", include("accounts.urls")),
    path("", include("registrations.urls")),
    path("gestao/", include("operations.urls")),
    path("health/live/", liveness, name="health-live"),
    path("health/ready/", readiness, name="health-ready"),
    path("gestao/", admin.site.urls),
    path("", public_page, name="portal-home"),
    path("pt-br/", public_page, {"language": "pt-br"}),
    path("style.css", public_asset, {"asset": "style.css"}),
    path("script.js", public_asset, {"asset": "script.js"}),
    path("images/<path:path>", public_image),
    path("sistema-login.html", RedirectView.as_view(pattern_name="accounts:login")),
    path("pt-br/sistema-login.html", RedirectView.as_view(pattern_name="accounts:login")),
    path("sistema-submissao.html", RedirectView.as_view(pattern_name="accounts:dashboard")),
    path("pt-br/sistema-submissao.html", RedirectView.as_view(pattern_name="accounts:dashboard")),
    path("<slug:page>.html", public_page),
    path("pt-br/<slug:page>.html", public_page, {"language": "pt-br"}),
]
