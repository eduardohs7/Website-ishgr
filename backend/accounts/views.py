from django.utils.translation import gettext_lazy as _
from functools import wraps

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import LoginView, LogoutView
from django.http import HttpResponseBadRequest
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils import translation
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from django.views.decorators.http import require_http_methods

from .forms import ConfirmationForm, EmailRequestForm, ParticipantLoginForm, ParticipantPasswordResetForm, ProfileForm, SignupForm
from .models import AccountActionToken, ParticipantProfile, User
from .services import active_token, consume_token, register_participant, request_account_email, update_profile


def verified_participant(view):
    @login_required
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.user.email_verified_at is None:
            return redirect("accounts:resend_confirmation")
        return view(request, *args, **kwargs)
    return wrapped


@never_cache
@sensitive_post_parameters("password1", "password2")
@require_http_methods(["GET", "POST"])
def signup(request):
    form = SignupForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        register_participant(
            email=form.cleaned_data["email"], full_name=form.cleaned_data["full_name"],
            password=form.cleaned_data["password1"],
            language=request.LANGUAGE_CODE,
        )
        return redirect("accounts:signup_done")
    return render(request, "accounts/form.html", {"form": form, "title": _("Criar conta"), "button": _("Criar conta")})


class ParticipantLoginView(LoginView):
    authentication_form = ParticipantLoginForm
    template_name = "accounts/login.html"


class ParticipantLogoutView(LogoutView):
    next_page = reverse_lazy("accounts:login")


@never_cache
@require_http_methods(["GET"])
def notice(request, kind):
    notices = {
        "signup": (_("Confira seu e-mail"), _("Se os dados permitirem o cadastro, enviaremos um link de confirmação. Se você já tiver conta, entre ou recupere sua senha.")),
        "confirm": (_("Confira seu e-mail"), _("Se houver uma conta aguardando confirmação, enviaremos um novo link. Confira também a pasta de spam.")),
        "reset": (_("Confira seu e-mail"), _("Se houver uma conta confirmada com esse endereço, enviaremos instruções de recuperação. Confira também a pasta de spam.")),
        "reset_complete": (_("Senha atualizada"), _("Sua senha foi atualizada. Entre novamente para acessar sua conta.")),
    }
    title, text = notices[kind]
    return render(request, "accounts/notice.html", {"title": title, "text": text})


@never_cache
@require_http_methods(["GET", "POST"])
def email_request(request, purpose):
    form = EmailRequestForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        request_account_email(form.cleaned_data["email"], purpose)
        return redirect("accounts:confirmation_sent" if purpose == "confirm" else "accounts:reset_sent")
    return render(request, "accounts/form.html", {
        "form": form,
        "title": _("Reenviar confirmação") if purpose == "confirm" else _("Recuperar senha"),
        "button": _("Enviar instruções"),
    })


@never_cache
@sensitive_post_parameters("token")
@require_http_methods(["GET", "POST"])
def confirm_email(request):
    form = ConfirmationForm(request.POST if request.method == "POST" else None)
    if request.method == "POST" and form.is_valid():
        if consume_token(form.cleaned_data["token"], AccountActionToken.Purpose.CONFIRM):
            messages.success(request, _("E-mail confirmado. Você já pode entrar na sua conta."))
            return redirect("accounts:login")
        form.add_error(None, _("Código inválido, expirado ou já utilizado. Solicite uma nova confirmação."))
    return render(request, "accounts/form.html", {"form": form, "title": _("Confirmar e-mail"), "button": _("Confirmar e-mail")})


@never_cache
@sensitive_post_parameters("token", "new_password1", "new_password2")
@sensitive_variables("raw_token", "candidate")
@require_http_methods(["GET", "POST"])
def reset_password(request):
    raw_token = request.POST.get("token", "") if request.method == "POST" else ""
    candidate = active_token(raw_token, AccountActionToken.Purpose.RESET)
    form = ParticipantPasswordResetForm(
        candidate.user if candidate else User(),
        request.POST if request.method == "POST" else None,
    )
    if request.method == "POST" and form.is_valid():
        if consume_token(raw_token, AccountActionToken.Purpose.RESET, password=form.cleaned_data["new_password1"]):
            return redirect("accounts:reset_complete")
        form.add_error(None, _("Código inválido, expirado ou já utilizado. Solicite uma nova recuperação."))
    return render(request, "accounts/form.html", {"form": form, "title": _("Redefinir senha"), "button": _("Salvar nova senha")})


@never_cache
@verified_participant
@require_http_methods(["GET"])
def dashboard(request):
    return render(request, "accounts/dashboard.html")


@never_cache
@verified_participant
@require_http_methods(["GET", "POST"])
def profile(request):
    instance = ParticipantProfile.objects.filter(user=request.user).first() or ParticipantProfile(user=request.user)
    form = ProfileForm(
        request.POST if request.method == "POST" else None,
        instance=instance, initial={"full_name": request.user.full_name},
    )
    if request.method == "POST" and form.is_valid():
        update_profile(request.user, form.cleaned_data)
        translation.activate(form.cleaned_data["preferred_language"])
        messages.success(request, _("Perfil atualizado."))
        return redirect("accounts:profile")
    return render(request, "accounts/profile.html", {"form": form, "title": _("Meu perfil")})


@never_cache
@require_http_methods(["POST"])
def set_language(request):
    language = request.POST.get("language", "")
    if language not in dict(settings.LANGUAGES):
        return HttpResponseBadRequest("Unsupported language")
    if request.user.is_authenticated:
        ParticipantProfile.objects.update_or_create(user=request.user, defaults={"preferred_language": language})
    target = request.POST.get("next", "")
    if not url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()):
        target = "/participante/" if request.user.is_authenticated else "/conta/entrar/"
    response = redirect(target or "/conta/entrar/")
    response.set_cookie(settings.LANGUAGE_COOKIE_NAME, language, max_age=31536000,
                        secure=settings.LANGUAGE_COOKIE_SECURE, httponly=True, samesite="Lax")
    return response
