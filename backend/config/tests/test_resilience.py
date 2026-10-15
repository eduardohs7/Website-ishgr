from contextlib import redirect_stderr
import io
import json
import os
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from unittest.mock import patch

from django.test import SimpleTestCase

from resilience.fixtures import guarded_directory
from resilience.runner import arguments, child_environment, HTTPClient, Sample, summarize


class ResilienceSafetyTests(SimpleTestCase):
    def test_cli_rejects_remote_targets_and_unbounded_workloads(self):
        for argv in (["--url", "https://example.org"], ["--duration", "0"], ["--duration", "60"], ["--concurrency", "100"]):
            with self.subTest(argv=argv), redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as error:
                    arguments(argv)
                self.assertEqual(error.exception.code, 2)

    def test_subprocesses_do_not_inherit_production_connections_or_runtime_overrides(self):
        values = {
            "CHAGS_DB_HOST": "production.example.org", "CHAGS_DB_PASSWORD": "synthetic-private-value",
            "CHAGS_SECRET_KEY": "synthetic-private-value", "PGHOST": "production.example.org",
            "DJANGO_SETTINGS_MODULE": "config.settings.production", "GUNICORN_CMD_ARGS": "--bind 0.0.0.0:80",
            "PYTHONPATH": "/untrusted/path", "PYTHONOPTIMIZE": "1", "GITHUB_ENV": "/untrusted/file",
        }
        with patch.dict(os.environ, values, clear=True):
            env = child_environment(Path("/workspace/private-test-directory"))
        for key in ("CHAGS_DB_HOST", "CHAGS_DB_PASSWORD", "PGHOST", "GUNICORN_CMD_ARGS", "PYTHONOPTIMIZE", "GITHUB_ENV"):
            self.assertNotIn(key, env)
        self.assertEqual(env["DJANGO_SETTINGS_MODULE"], "resilience_settings")
        self.assertNotIn("synthetic-private-value", json.dumps(env))
        self.assertNotIn("/untrusted/", json.dumps(env))

    def test_fixtures_refuse_existing_application_settings_before_querying_database(self):
        with patch.dict(os.environ, {"CHAGS_RESILIENCE_DIR": "/tmp", "DJANGO_SETTINGS_MODULE": "config.settings.production"}):
            with self.assertRaisesMessage(RuntimeError, "Ambiente descartável"):
                guarded_directory()

    def test_report_percentiles_and_rate_exclude_response_contents(self):
        samples = [Sample(200, {"password": "synthetic-private-value"}, value, None) for value in range(1, 21)]
        summary = summarize(samples, 2.0, 4)
        self.assertEqual(summary["p50_ms"], 10)
        self.assertEqual(summary["p95_ms"], 19)
        self.assertEqual(summary["max_ms"], 20)
        self.assertEqual(summary["requests_per_second"], 10)
        self.assertEqual(summary["statuses"], {"200": 20})
        self.assertNotIn("synthetic-private-value", json.dumps(summary))

    def test_http_client_rejects_nonlocal_and_ambiguous_origins(self):
        for origin in ("http://example.org:8000", "https://127.0.0.1:8000", "http://user:password@127.0.0.1:8000", "http://127.0.0.1:8000/path"):
            with self.subTest(origin=origin), self.assertRaises(RuntimeError):
                HTTPClient(origin)

    def test_http_transport_ignores_proxies_and_does_not_follow_redirects(self):
        received = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                received.append(self.path)
                if self.path == "/redirect":
                    self.send_response(302)
                    self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/destination")
                    self.end_headers()
                else:
                    self.send_response(200)
                    self.end_headers()

            def log_message(self, *args):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with patch.dict(os.environ, {"http_proxy": "http://127.0.0.1:1", "HTTP_PROXY": "http://127.0.0.1:1", "no_proxy": "", "NO_PROXY": ""}):
                    response = HTTPClient(f"http://127.0.0.1:{server.server_port}").request("/redirect")
                self.assertEqual(response.status, 302)
                self.assertEqual(received, ["/redirect"])
            finally:
                server.shutdown()
                thread.join(timeout=5)
