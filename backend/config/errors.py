"""Generic API failures in production; retain Django's existing HTML defaults."""
from django.views import defaults

from accounts.api_protocol import error_response, is_api_request


def api_error(request, code, status):
    if is_api_request(request):
        response = error_response(code, status=status)
        response["Cache-Control"] = "no-store"
        return response
    return None


def bad_request(request, exception):
    response = api_error(request, "bad_request", 400)
    return response if response is not None else defaults.bad_request(request, exception)


def permission_denied(request, exception):
    response = api_error(request, "permission_denied", 403)
    return response if response is not None else defaults.permission_denied(request, exception)


def page_not_found(request, exception):
    response = api_error(request, "not_found", 404)
    return response if response is not None else defaults.page_not_found(request, exception)


def server_error(request):
    response = api_error(request, "internal_error", 500)
    return response if response is not None else defaults.server_error(request)
