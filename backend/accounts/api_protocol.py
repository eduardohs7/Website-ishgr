"""Shared request/response contract for the same-origin account API."""
import json
from functools import wraps

from django.http import JsonResponse
from django.views.csrf import csrf_failure as html_csrf_failure
from django.views.decorators.cache import never_cache
from django.views.decorators.debug import sensitive_variables

API_PREFIXES = ("/api/v1/auth/", "/api/v1/participant/")
MAX_BODY_BYTES = 32 * 1024


class PayloadError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status


def error_response(code, *, status, errors=None):
    error = {"code": code}
    if errors is not None:
        error["fields"] = errors
    return JsonResponse({"ok": False, "error": error}, status=status)


def success_response(data=None, *, status=200):
    return JsonResponse({"ok": True, "data": data or {}}, status=status)


def participant_access_error(user):
    if not user.is_authenticated or not user.is_active:
        return error_response("authentication_required", status=401)
    if user.email_verified_at is None:
        return error_response("email_not_verified", status=403)
    return None


def verified_api_participant(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        error = participant_access_error(request.user)
        if error is not None:
            return error
        return view(request, *args, **kwargs)
    return wrapped


@sensitive_variables("*")
def request_data(request):
    """Parse once: the limiter and view must use the same submitted identity."""
    if hasattr(request, "account_api_data"):
        return request.account_api_data
    content_length = request.META.get("CONTENT_LENGTH", "")
    try:
        if content_length and int(content_length) > MAX_BODY_BYTES:
            raise PayloadError("payload_too_large", 413)
    except ValueError:
        raise PayloadError("invalid_payload") from None
    if request.content_type == "application/json":
        if len(request.body) > MAX_BODY_BYTES:
            raise PayloadError("payload_too_large", 413)
        try:
            # Reject duplicate keys instead of silently choosing an identity.
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate key")
                    result[key] = value
                return result

            data = json.loads(request.body, object_pairs_hook=unique_object)
        except (ValueError, UnicodeError, RecursionError):
            raise PayloadError("invalid_payload") from None
        if not isinstance(data, dict):
            raise PayloadError("invalid_payload")
    elif request.content_type == "application/x-www-form-urlencoded":
        if any(len(values) != 1 for _, values in request.POST.lists()):
            raise PayloadError("invalid_payload")
        data = request.POST.dict()
        # The CSRF middleware validates this field; it isn't a business input.
        data.pop("csrfmiddlewaretoken", None)
    else:
        raise PayloadError("unsupported_media_type", 415)
    if any(not isinstance(value, str) for value in data.values()):
        # All fields are strings, including remember_me ("true" / "false").
        raise PayloadError("invalid_payload")
    request.account_api_data = data
    return data


def api_methods(*methods):
    def decorate(view):
        @never_cache
        @wraps(view)
        @sensitive_variables("*")
        def wrapped(request, *args, **kwargs):
            if request.method not in methods:
                response = error_response("method_not_allowed", status=405)
                response["Allow"] = ", ".join(methods)
                return response
            try:
                return view(request, *args, **kwargs)
            except PayloadError as error:
                return error_response(error.code, status=error.status)
        return wrapped
    return decorate


def form_errors(form):
    # Stable validation codes accompany translated messages for the UI.
    return {
        field: [{"code": item["code"], "message": item["message"]} for item in items]
        for field, items in form.errors.get_json_data().items()
    }


def csrf_failure(request, reason=""):
    if request.path.startswith(API_PREFIXES):
        response = error_response("csrf_failed", status=403)
        response["Cache-Control"] = "no-store"
        return response
    return html_csrf_failure(request, reason=reason)
