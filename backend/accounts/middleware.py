from datetime import timedelta
from ipaddress import ip_address, ip_network

from django.conf import settings
from django.db import transaction
from django.shortcuts import render
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.views.decorators.debug import sensitive_variables

from .models import AuthThrottleBucket, User
from .api_protocol import PayloadError, error_response, request_data

ROUTE_SCOPES = {
    "admin:login": "login", "accounts:login": "login", "accounts:signup": "signup",
    "accounts:request_reset": "request_email", "accounts:resend_confirmation": "request_email",
    "accounts:confirm": "confirm", "accounts:reset": "reset",
    "auth_api:login": "login", "auth_api:signup": "signup",
    "auth_api:request_reset": "request_email", "auth_api:resend_confirmation": "request_email",
    "auth_api:confirm": "confirm", "auth_api:reset": "reset",
}


def client_ip(request):
    try:
        remote = ip_address(request.META.get("REMOTE_ADDR", ""))
    except ValueError:
        return "unknown"
    networks = [ip_network(value) for value in settings.AUTH_TRUSTED_PROXY_NETWORKS]

    def trusted(ip):
        return any(ip in network for network in networks)

    if not trusted(remote):
        return str(remote)
    header = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if not header or len(header) > 1024:
        return str(remote)
    try:
        chain = [ip_address(part.strip()) for part in header.split(",")] + [remote]
    except ValueError:
        return str(remote)
    if len(chain) > 10:
        return str(remote)
    while len(chain) > 1 and trusted(chain[-1]):
        chain.pop()
    return str(chain[-1])


class AccountRateLimitMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        return self.get_response(request)

    @sensitive_variables("payload")
    def process_view(self, request, view_func, view_args, view_kwargs):
        if request.method != "POST":
            return None
        scope = ROUTE_SCOPES.get(request.resolver_match.view_name)
        if not scope:
            return None
        request.sensitive_post_parameters = (
            "password", "password1", "password2", "new_password1", "new_password2", "token",
        )
        policies = settings.ACCOUNT_RATE_LIMITS[scope]
        values = {"ip": client_ip(request)}
        is_api = request.resolver_match.namespace == "auth_api"
        if is_api:
            try:
                payload = request_data(request)
            except PayloadError as error:
                response = error_response(error.code, status=error.status)
                response["Cache-Control"] = "no-store"
                return response
            email = payload.get("email", "")
        else:
            identity_field = "username" if scope == "login" else "email"
            email = request.POST.get(identity_field, "")
        values["identity"] = User.objects.normalize_email(email)[:254]
        now = timezone.now()
        retry_after = 0
        # The same PostgreSQL buckets serve all workers and both login endpoints.
        with transaction.atomic():
            keys = [
                (salted_hmac("accounts.throttle", f"{scope}:{kind}:{values[kind]}", algorithm="sha256").hexdigest(), rule)
                for kind, rule in policies.items()
            ]
            for key, (limit, seconds) in sorted(keys):
                bucket, _ = AuthThrottleBucket.objects.get_or_create(key=key)
                bucket = AuthThrottleBucket.objects.select_for_update().get(pk=bucket.pk)
                if bucket.window_start + timedelta(seconds=seconds) <= now:
                    bucket.window_start, bucket.count = now, 0
                bucket.count = min(bucket.count + 1, limit + 1)
                bucket.save(update_fields=["window_start", "count"])
                if bucket.count > limit:
                    retry_after = max(retry_after, int((bucket.window_start + timedelta(seconds=seconds) - now).total_seconds()) + 1)
        if retry_after:
            response = error_response("rate_limited", status=429) if is_api else render(request, "accounts/rate_limited.html", status=429)
            response["Retry-After"] = str(retry_after)
            response["Cache-Control"] = "no-store"
            return response
        return None
