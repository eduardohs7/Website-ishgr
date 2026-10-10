from django.urls import path

from . import views

app_name = "registrations"
urlpatterns = [
    path("participante/inscricao/", views.mine, name="mine"),
    path("participante/jems/", views.jems, name="jems"),
]
