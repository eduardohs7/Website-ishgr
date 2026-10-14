import base64
from concurrent.futures import ThreadPoolExecutor
import io
import json
import logging
import os
from pathlib import Path
import secrets
import subprocess
import sys
from threading import Barrier
from unittest.mock import patch

from django.core.exceptions import BadRequest, PermissionDenied
from django.http import Http404, HttpResponse
from django.test import Client, RequestFactory, SimpleTestCase, override_settings
from django.urls import path

from config.observability import RequestDiagnosticsMiddleware, SafeJSONFormatter, current_request_id


SYNTHETIC_PRIVATE = "synthetic-private-value"


def probe(request, value=""):
    return HttpResponse("ok")


def failure(request, kind, value=""):
    errors = {"400": BadRequest, "403": PermissionDenied, "404": Http404, "500": RuntimeError}
    # Exercise messages, formatting args, arbitrary extras and a body in production.
    logging.getLogger("chags.probe").error(
        "Private message: %s", SYNTHETIC_PRIVATE,
        extra={"payload": request.body, "authorization": SYNTHETIC_PRIVATE},
    )
    raise errors[kind](SYNTHETIC_PRIVATE)


urlpatterns = [
    path("api/v1/probe/<slug:value>/", probe, name="probe"),
    path("api/v1/failure/<str:kind>/<slug:value>/", failure, name="failure"),
    path("failure/<str:kind>/", failure, name="html-failure"),
]
handler400 = "config.errors.bad_request"
handler403 = "config.errors.permission_denied"
handler404 = "config.errors.page_not_found"
handler500 = "config.errors.server_error"


@override_settings(
    DEBUG=False,
    ROOT_URLCONF=__name__,
    ALLOWED_HOSTS=["testserver"],
    MIDDLEWARE=[
        "config.observability.RequestDiagnosticsMiddleware",
        "django.middleware.security.SecurityMiddleware",
        "django.middleware.common.CommonMiddleware",
        "django.middleware.csrf.CsrfViewMiddleware",
    ],
)
class APIErrorTests(SimpleTestCase):
    def test_generic_errors_are_json_private_and_not_cached(self):
        client = Client(raise_request_exception=False)
        cases = {400: "bad_request", 403: "permission_denied", 404: "not_found", 500: "internal_error"}
        with self.assertLogs("chags.probe", level="ERROR"):
            for status, code in cases.items():
                with self.subTest(status=status):
                    response = client.get(f"/api/v1/failure/{status}/{SYNTHETIC_PRIVATE}/")
                    self.assertEqual(response.status_code, status)
                    self.assertEqual(response.json(), {"ok": False, "error": {"code": code}})
                    self.assertIn("no-store", response["Cache-Control"])
                    self.assertNotIn(SYNTHETIC_PRIVATE.encode(), response.content)
                    self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_unknown_api_versions_and_paths_use_generic_json(self):
        for url in ("/api", "/api/v2/missing/", f"/api/{SYNTHETIC_PRIVATE}?token={SYNTHETIC_PRIVATE}"):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"]["code"], "not_found")
                self.assertIn("no-store", response["Cache-Control"])

    @override_settings(ROOT_URLCONF="config.urls")
    def test_application_root_registers_the_error_handlers(self):
        response = self.client.get("/api/v1/missing/")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["code"], "not_found")

    def test_rejected_host_has_generic_json_and_request_id(self):
        response = self.client.get("/api/v1/probe/example/", HTTP_HOST="invalid.example.org")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "bad_request")
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_html_routes_retain_html_errors(self):
        client = Client(raise_request_exception=False)
        with self.assertLogs("chags.probe", level="ERROR"):
            response = client.get("/failure/500/")
        self.assertEqual(response.status_code, 500)
        self.assertTrue(response["Content-Type"].startswith("text/html"))
        self.assertNotIn(SYNTHETIC_PRIVATE.encode(), response.content)
        response = client.get("/not-an-api-route/")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(response["Content-Type"].startswith("text/html"))

    def test_csrf_keeps_existing_api_contract_and_correlation(self):
        response = Client(enforce_csrf_checks=True).post("/api/v1/probe/example/", {})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "csrf_failed")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_client_ids_are_ignored_and_request_context_is_reset(self):
        ids = []
        for _ in range(2):
            response = self.client.get(
                "/api/v1/probe/example/", HTTP_X_REQUEST_ID="0" * 32, HTTP_X_CORRELATION_ID=SYNTHETIC_PRIVATE,
            )
            ids.append(response["X-Request-ID"])
            self.assertRegex(ids[-1], r"^[0-9a-f]{32}$")
            self.assertIsNone(current_request_id.get())
        self.assertNotEqual(ids[0], ids[1])
        self.assertNotIn("0" * 32, ids)

    @override_settings(SECURE_SSL_REDIRECT=True)
    def test_early_https_redirect_has_request_id(self):
        response = self.client.get("/api/v1/probe/example/")
        self.assertEqual(response.status_code, 301)
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{32}$")

    @override_settings(DEBUG=True)
    def test_debug_retains_local_django_diagnostics(self):
        response = self.client.get("/api/v1/missing/")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(response["Content-Type"].startswith("text/html"))


class SafeLoggingTests(SimpleTestCase):
    def test_formatter_never_formats_message_or_arbitrary_request_data(self):
        class MustNotFormat:
            def __str__(self):
                raise AssertionError("Logging must not format the original message/args")

        request = RequestFactory().post(f"/api/{SYNTHETIC_PRIVATE}?token={SYNTHETIC_PRIVATE}", {
            "password": SYNTHETIC_PRIVATE,
        }, HTTP_AUTHORIZATION=SYNTHETIC_PRIVATE, HTTP_COOKIE=SYNTHETIC_PRIVATE)
        request.request_id = "a" * 32
        record = logging.LogRecord("django.request", logging.ERROR, __file__, 1, MustNotFormat(), (MustNotFormat(),), None)
        record.request = request
        record.private_extra = SYNTHETIC_PRIVATE
        record.exc_text = SYNTHETIC_PRIVATE
        record.stack_info = SYNTHETIC_PRIVATE
        try:
            raise RuntimeError(SYNTHETIC_PRIVATE)
        except RuntimeError:
            record.exc_info = sys.exc_info()
        serialized = SafeJSONFormatter().format(record)
        data = json.loads(serialized)
        self.assertNotIn(SYNTHETIC_PRIVATE, serialized)
        self.assertEqual(data["request_id"], "a" * 32)
        self.assertEqual(data["exception_type"], "RuntimeError")
        self.assertEqual(set(data["stack"][-1]), {"module", "function", "line"})
        self.assertNotIn("message", data)

    def test_request_summary_has_static_route_and_no_user_values(self):
        with self.assertLogs("chags.http", level="INFO") as logs:
            request = RequestFactory().get(f"/api/v1/item/{SYNTHETIC_PRIVATE}/?token={SYNTHETIC_PRIVATE}")
            request.resolver_match = type("Match", (), {"view_name": "participant_api:item"})()
            RequestDiagnosticsMiddleware(lambda request: HttpResponse("ok"))(request)
        data = json.loads(SafeJSONFormatter().format(logs.records[0]))
        self.assertNotIn(SYNTHETIC_PRIVATE, json.dumps(data))
        self.assertEqual(data["route"], "participant_api:item")
        self.assertEqual(data["method"], "GET")
        self.assertEqual(data["status"], 200)
        self.assertGreaterEqual(data["duration_ms"], 0)
        self.assertIsNone(current_request_id.get())

    def test_context_is_reset_after_an_unconverted_exception(self):
        def fail(request):
            raise RuntimeError(SYNTHETIC_PRIVATE)
        with self.assertRaises(RuntimeError):
            RequestDiagnosticsMiddleware(fail)(RequestFactory().get("/"))
        self.assertIsNone(current_request_id.get())

    def test_concurrent_requests_keep_independent_logging_contexts(self):
        barrier = Barrier(2, timeout=5)
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.setFormatter(SafeJSONFormatter())
        logger = logging.getLogger("chags.thread-probe")
        with patch.object(logger, "handlers", [handler]), patch.object(logger, "propagate", False), patch.object(logger, "level", logging.WARNING):
            def respond(request):
                barrier.wait()
                logger.warning(SYNTHETIC_PRIVATE)
                return HttpResponse("ok")
            def run(_):
                response = RequestDiagnosticsMiddleware(respond)(RequestFactory().get("/"))
                self.assertIsNone(current_request_id.get())
                return response["X-Request-ID"]
            with ThreadPoolExecutor(max_workers=2) as pool:
                ids = list(pool.map(run, range(2)))
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertEqual({row["request_id"] for row in rows}, set(ids))
        self.assertEqual(len(set(ids)), 2)
        self.assertNotIn(SYNTHETIC_PRIVATE, output.getvalue())

    def test_actual_production_logging_redacts_failures_and_raw_server_messages(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith("CHAGS_")}
        env.update(
            DJANGO_SETTINGS_MODULE="config.settings.production",
            CHAGS_SECRET_KEY=secrets.token_urlsafe(64),
            CHAGS_ALLOWED_HOSTS="testserver",
            CHAGS_DB_NAME="synthetic_database", CHAGS_DB_USER="synthetic_user",
            CHAGS_DB_PASSWORD="synthetic-not-a-real-secret", CHAGS_DB_HOST="database.example.org",
            CHAGS_PUBLIC_URL="https://testserver", CHAGS_EMAIL_HOST="smtp.example.org",
            CHAGS_DEFAULT_FROM_EMAIL="no-reply@example.org",
            CHAGS_MFA_ENCRYPTION_KEY=base64.urlsafe_b64encode(b"0" * 32).decode("ascii"),
        )
        script = '''
import django, logging
django.setup()
from django.test import Client, override_settings
from config.observability import current_request_id
from config.tests.test_observability import SYNTHETIC_PRIVATE as private
middleware = ["config.observability.RequestDiagnosticsMiddleware", "django.middleware.security.SecurityMiddleware", "django.middleware.common.CommonMiddleware", "django.middleware.csrf.CsrfViewMiddleware"]
with override_settings(ROOT_URLCONF="config.tests.test_observability", MIDDLEWARE=middleware, SECURE_SSL_REDIRECT=False):
    client = Client(raise_request_exception=False)
    response = client.post(f"/api/v1/failure/500/{private}/?token={private}", {"password": private}, HTTP_AUTHORIZATION=private, HTTP_COOKIE=f"sessionid={private}")
    assert response.status_code == 500
    assert response.json() == {"ok": False, "error": {"code": "internal_error"}}
    assert private.encode() not in response.content
    assert current_request_id.get() is None
    response = client.get(f"/api/{private}")
    assert response.status_code == 404
    response = client.get("/api/v1/probe/example/", HTTP_HOST="invalid.example.org")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "bad_request"
    response = client.get("/api/v1/probe/example/")
    assert response.status_code == 200
logging.getLogger("django.server").error("raw request %s", private)
logging.getLogger("background-probe").error("raw background %s", private)
'''
        result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[2],
                                env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(SYNTHETIC_PRIVATE, result.stdout + result.stderr)
        self.assertNotIn("invalid.example.org", result.stdout + result.stderr)
        self.assertEqual(result.stderr, "")
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        responses = [row for row in rows if row["event"] == "http_response"]
        self.assertEqual([row["status"] for row in responses], [500, 404, 400, 200])
        self.assertEqual(len({row["request_id"] for row in responses}), 4)
        error = next(row for row in rows if row.get("exception_type") == "RuntimeError")
        self.assertEqual(error["request_id"], responses[0]["request_id"])
        self.assertTrue(error["stack"])
        self.assertNotIn("request_id", rows[-1])
