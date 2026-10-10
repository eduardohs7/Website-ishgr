from django.urls import path

from . import views

app_name = "accounts"
urlpatterns = [
    path("conta/idioma/", views.set_language, name="set_language"),
    path("conta/cadastro/", views.signup, name="signup"),
    path("conta/cadastro/concluido/", views.notice, {"kind": "signup"}, name="signup_done"),
    path("conta/entrar/", views.ParticipantLoginView.as_view(), name="login"),
    path("conta/sair/", views.ParticipantLogoutView.as_view(), name="logout"),
    path("conta/confirmar-email/", views.confirm_email, name="confirm"),
    path("conta/reenviar-confirmacao/", views.email_request, {"purpose": "confirm"}, name="resend_confirmation"),
    path("conta/confirmacao-enviada/", views.notice, {"kind": "confirm"}, name="confirmation_sent"),
    path("conta/recuperar-senha/", views.email_request, {"purpose": "reset"}, name="request_reset"),
    path("conta/recuperacao-enviada/", views.notice, {"kind": "reset"}, name="reset_sent"),
    path("conta/redefinir-senha/", views.reset_password, name="reset"),
    path("conta/senha-atualizada/", views.notice, {"kind": "reset_complete"}, name="reset_complete"),
    path("participante/", views.dashboard, name="dashboard"),
    path("participante/perfil/", views.profile, name="profile"),
]
