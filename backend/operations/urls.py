from django.urls import path

from . import views

app_name = "operations"
urlpatterns = [
    path("inscricoes/", views.registrations, name="registrations"),
    path("inscricoes/<uuid:registration_id>/", views.registration, name="registration"),
    path("indicadores/", views.indicators, name="indicators"),
    path("exportacoes/", views.exports, name="exports"),
]
