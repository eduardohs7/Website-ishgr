import os
import secrets
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.settings.environment import database_settings, development_secret_key


class EnvironmentTests(SimpleTestCase):
    def test_development_key_is_private_random_and_persistent(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"CHAGS_SECRET_KEY": ""}):
                first = development_secret_key(Path(directory))
                second = development_secret_key(Path(directory))
            self.assertEqual(first, second)
            self.assertGreaterEqual(len(first), 50)
            path = Path(directory) / ".local" / "django-secret-key"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_production_database_requires_configuration_and_tls(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesMessage(ImproperlyConfigured, "CHAGS_DB_NAME"):
                database_settings(production=True)
        values = {
            "CHAGS_DB_NAME": "synthetic_test_database",
            "CHAGS_DB_USER": "synthetic_user",
            "CHAGS_DB_PASSWORD": "synthetic-not-a-real-secret",
            "CHAGS_DB_HOST": "database.example.org",
            "CHAGS_DB_SSLMODE": "disable",
        }
        with patch.dict(os.environ, values, clear=True):
            with self.assertRaisesMessage(ImproperlyConfigured, "TLS"):
                database_settings(production=True)

    def test_production_settings_fail_closed_without_a_secret(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith("CHAGS_")}
        result = subprocess.run(
            [sys.executable, "-c", "import config.settings.production"],
            cwd=Path(__file__).resolve().parents[2],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Configure a variável CHAGS_SECRET_KEY", result.stderr)

    def test_production_requires_hosts_and_has_secure_defaults(self):
        env = {key: value for key, value in os.environ.items() if not key.startswith("CHAGS_")}
        env.update(
            CHAGS_SECRET_KEY=secrets.token_urlsafe(64),
            CHAGS_DB_NAME="synthetic_database",
            CHAGS_DB_USER="synthetic_user",
            CHAGS_DB_PASSWORD="synthetic-not-a-real-secret",
            CHAGS_DB_HOST="database.example.org",
            CHAGS_PUBLIC_URL="https://portal.example.org",
            CHAGS_EMAIL_HOST="smtp.example.org",
            CHAGS_DEFAULT_FROM_EMAIL="no-reply@example.org",
        )
        script = (
            "import config.settings.production as s; "
            "assert not s.DEBUG; "
            "assert s.SESSION_COOKIE_SECURE and s.CSRF_COOKIE_SECURE; "
            "assert s.SECURE_SSL_REDIRECT; "
            "assert not hasattr(s, 'SECURE_PROXY_SSL_HEADER')"
        )
        for hosts, expected in (("", False), ("*", False), ("portal.example.org", True)):
            with self.subTest(hosts=hosts):
                env["CHAGS_ALLOWED_HOSTS"] = hosts
                result = subprocess.run(
                    [sys.executable, "-c", script],
                    cwd=Path(__file__).resolve().parents[2],
                    env=env,
                    capture_output=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode == 0, expected)
