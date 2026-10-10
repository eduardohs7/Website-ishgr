"""Session-based authentication API for the independently maintained front."""
from django.conf import settings
from django.contrib.auth import login as auth_login, logout as auth_logout
from django.middleware.csrf import get_token
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables

from .api_forms import ApiLoginInputForm, ApiResetForm, ApiSignupForm
from .api_protocol import api_methods, error_response, form_errors, request_data, success_response
from .forms import ConfirmationForm, EmailRequestForm, ParticipantLoginForm, ParticipantPasswordResetForm
from .models import AccountActionToken, User
from .services import active_token, consume_token, register_participant, request_account_email


def participant_data(user):
    return {"id": str(user.pk), "full_name": user.full_name, "email": user.email}


@api_methods("GET")
def session(request):
    authenticated = request.user.is_authenticated and request.user.email_verified_at is not None
    return success_response({
        "authenticated": bool(authenticated),
        "participant": participant_data(request.user) if authenticated else None,
        "csrf_token": get_token(request),
    })


@api_methods("POST")
@sensitive_post_parameters("*")
@sensitive_variables("*")
def signup(request):
    data = request_data(request)
    allowed = {"first_name", "last_name", "email", "password1", "password2", "institution", "country", "language"}
    if data.keys() - allowed:
        return error_response("unknown_fields", status=400)
    form = ApiSignupForm({"language": request.LANGUAGE_CODE, **data})
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    register_participant(
        email=form.cleaned_data["email"], full_name=form.cleaned_data["full_name"],
        password=form.cleaned_data["password1"], language=form.cleaned_data["language"],
        institution=form.cleaned_data["institution"], country=str(form.cleaned_data["country"] or ""),
    )
    # Deliberately identical for an existing address: no user ID or token.
    return success_response({"code": "signup_received"}, status=202)


@api_methods("POST")
@sensitive_post_parameters("*")
@sensitive_variables("*")
def login(request):
    data = request_data(request)
    if data.keys() - {"email", "password", "remember_me", "next"}:
        return error_response("unknown_fields", status=400)
    inputs = ApiLoginInputForm(data)
    if not inputs.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(inputs))
    form = ParticipantLoginForm(request, data={
        "username": inputs.cleaned_data["email"], "password": inputs.cleaned_data["password"],
    })
    if not form.is_valid():
        return error_response("invalid_credentials", status=401)
    user = form.get_user()
    auth_login(request, user)
    request.session.set_expiry(settings.SESSION_COOKIE_AGE if inputs.cleaned_data["remember_me"] == "true" else 0)
    target = inputs.cleaned_data["next"]
    if not target or not url_has_allowed_host_and_scheme(target, {request.get_host()}, require_https=request.is_secure()):
        target = reverse("accounts:dashboard")
    return success_response({"participant": participant_data(user), "next": target, "csrf_token": get_token(request)})


@api_methods("POST")
def logout(request):
    data = request_data(request)
    if data:
        return error_response("unknown_fields", status=400)
    auth_logout(request)
    return success_response({"code": "logged_out", "csrf_token": get_token(request)})


@api_methods("POST")
@sensitive_post_parameters("*")
@sensitive_variables("*")
def confirm_email(request):
    data = request_data(request)
    if data.keys() - {"token"}:
        return error_response("unknown_fields", status=400)
    form = ConfirmationForm(data)
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    if not consume_token(form.cleaned_data["token"], AccountActionToken.Purpose.CONFIRM):
        return error_response("invalid_token", status=400)
    return success_response({"code": "email_confirmed"})


@api_methods("POST")
def email_request(request, purpose):
    data = request_data(request)
    if data.keys() - {"email"}:
        return error_response("unknown_fields", status=400)
    form = EmailRequestForm(data)
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    request_account_email(form.cleaned_data["email"], purpose)
    return success_response({"code": "email_request_received"}, status=202)


@api_methods("POST")
@sensitive_post_parameters("*")
@sensitive_variables("*")
def reset_password(request):
    data = request_data(request)
    if data.keys() - {"token", "password1", "password2"}:
        return error_response("unknown_fields", status=400)
    inputs = ApiResetForm(data)
    if not inputs.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(inputs))
    candidate = active_token(inputs.cleaned_data["token"], AccountActionToken.Purpose.RESET)
    if not candidate:
        return error_response("invalid_token", status=400)
    form = ParticipantPasswordResetForm(candidate.user, data={
        "token": inputs.cleaned_data["token"],
        "new_password1": inputs.cleaned_data["password1"], "new_password2": inputs.cleaned_data["password2"],
    })
    if not form.is_valid():
        errors = form_errors(form)
        errors = {key.replace("new_password", "password"): value for key, value in errors.items()}
        return error_response("validation_error", status=422, errors=errors)
    if not consume_token(inputs.cleaned_data["token"], AccountActionToken.Purpose.RESET, password=form.cleaned_data["new_password1"]):
        return error_response("invalid_token", status=400)
    return success_response({"code": "password_updated"})
