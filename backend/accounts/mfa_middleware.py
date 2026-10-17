from django.db import DatabaseError, transaction
from django.shortcuts import redirect

from .api_protocol import error_response
from .mfa import access_code
from .models import User


def failure(code, status=403):
    response = error_response(code, status=status)
    response["Cache-Control"] = "no-store"
    return response


class StaffMFAMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path_info
        protected = path == "/gestao" or path.startswith(("/gestao/", "/api/v1/management/"))
        exempt = path in ("/gestao/login/", "/gestao/logout/")
        if not protected or exempt or not request.user.is_authenticated:
            return self.get_response(request)
        try:
            with transaction.atomic():
                # Also prevents factor revocation racing a protected write/export.
                request.user = User.objects.select_for_update().get(pk=request.user.pk)
                if request.user.is_active and request.user.is_staff:
                    code = access_code(request)
                    if code:
                        # Browser page requests should land on the MFA screen. API
                        # calls keep the documented JSON error contract.
                        accepts_html = "text/html" in request.headers.get("Accept", "")
                        is_api = path.startswith("/api/")
                        if request.method == "GET" and accepts_html and not is_api:
                            response = redirect("accounts:staff_mfa")
                            response["Cache-Control"] = "no-store"
                            return response
                        return failure(code)
                response = self.get_response(request)
                if response.status_code >= 500:
                    transaction.set_rollback(True)
                return response
        except User.DoesNotExist:
            return failure("authentication_required", 401)
        except DatabaseError:
            return failure("operation_unavailable", 503)
