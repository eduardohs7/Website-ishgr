from functools import wraps

from django import forms
from django.conf import settings
from django.db import DatabaseError
from django.middleware.csrf import get_token
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables

from . import mfa
from .api_protocol import api_methods, error_response, form_errors, request_data, success_response
from .models import StaffMFA, User


def staff_api(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_active:
            return error_response("authentication_required", status=401)
        if not request.user.is_staff:
            return error_response("permission_denied", status=403)
        return view(request, *args, **kwargs)
    return wrapped


class PasswordInput(forms.Form):
    password = forms.CharField(max_length=1024, strip=False)


class ConfirmInput(forms.Form):
    code = forms.RegexField(r"^[0-9]{6}$", max_length=6)


class VerifyInput(forms.Form):
    code = forms.CharField(max_length=64)
    method = forms.ChoiceField(choices=(("totp", "TOTP"), ("recovery", "Recovery")))


@api_methods("GET")
@staff_api
def status(request):
    if request.GET:
        return error_response("unknown_fields", status=400)
    factor = StaffMFA.objects.filter(user=request.user).first()
    enabled = bool(factor and factor.enabled)
    return success_response({"enabled": enabled, "required": enabled or settings.STAFF_MFA_REQUIRED,
                             "verified": mfa.verified(request, request.user, factor),
                             "recently_verified": mfa.verified(request, request.user, factor, recent=True),
                             "pending_enrollment": mfa.pending_valid(request, request.user, factor),
                             "recovery_codes_remaining": factor.recovery_codes.filter(used_at__isnull=True).count() if enabled else 0,
                             "csrf_token": get_token(request)})


@sensitive_variables("*")
def operation(request, form_class, service, *, defaults=None):
    if request.GET:
        return error_response("unknown_fields", status=400)
    data = request_data(request)
    if data.keys() - set(form_class.base_fields):
        return error_response("unknown_fields", status=400)
    form = form_class({**(defaults or {}), **data})
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    try:
        result = service(request, **form.cleaned_data)
    except User.DoesNotExist:
        return error_response("authentication_required", status=401)
    except mfa.MFAError as error:
        return error_response(error.code, status=error.status)
    except DatabaseError:
        return error_response("operation_unavailable", status=503)
    return success_response(result)


@api_methods("POST")
@staff_api
@sensitive_post_parameters("*")
def enroll(request):
    return operation(request, PasswordInput, mfa.begin)


@api_methods("POST")
@staff_api
@sensitive_post_parameters("*")
def confirm(request):
    return operation(request, ConfirmInput, mfa.confirm)


@api_methods("POST")
@staff_api
@sensitive_post_parameters("*")
def verify(request):
    return operation(request, VerifyInput, mfa.verify, defaults={"method": "totp"})


@api_methods("POST")
@staff_api
@sensitive_post_parameters("*")
def recovery_codes(request):
    return operation(request, PasswordInput, mfa.manage)


@api_methods("POST")
@staff_api
@sensitive_post_parameters("*")
def disable(request):
    return operation(request, PasswordInput, lambda request, password: mfa.manage(request, password, disable=True))
