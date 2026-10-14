from django import forms
from django.db import DatabaseError
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables

from . import security
from .api_protocol import api_methods, error_response, form_errors, request_data, success_response
from .models import User


class PasswordInput(forms.Form):
    current_password = forms.CharField(max_length=1024, strip=False)


class ChangePasswordInput(PasswordInput):
    new_password1 = forms.CharField(max_length=1024, strip=False)
    new_password2 = forms.CharField(max_length=1024, strip=False)


@sensitive_variables("*")
def operation(request, form_class, service):
    if not request.user.is_authenticated or not request.user.is_active:
        return error_response("authentication_required", status=401)
    if request.GET:
        return error_response("unknown_fields", status=400)
    data = request_data(request)
    if data.keys() - set(form_class.base_fields):
        return error_response("unknown_fields", status=400)
    form = form_class(data)
    if not form.is_valid():
        return error_response("validation_error", status=422, errors=form_errors(form))
    try:
        result = service(request, **form.cleaned_data)
    except User.DoesNotExist:
        return error_response("authentication_required", status=401)
    except security.SecurityError as error:
        return error_response(error.code, status=error.status, errors=error.errors)
    except DatabaseError:
        return error_response("operation_unavailable", status=503)
    return success_response(result)


@api_methods("POST")
@sensitive_post_parameters("*")
def change_password(request):
    return operation(request, ChangePasswordInput, security.change_password)


@api_methods("POST")
@sensitive_post_parameters("*")
def logout_all(request):
    return operation(request, PasswordInput, security.logout_all)
