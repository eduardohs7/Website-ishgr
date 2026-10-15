from unittest.mock import patch

from django.db import OperationalError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from accounts.models import User


class HealthTests(TestCase):
    def test_authenticated_health_probes_do_not_load_sessions_or_profiles(self):
        user = User.objects.create_user("health@example.invalid", full_name="Conta sintética")
        self.client.force_login(user)
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(reverse("health-live"))
        # Do not dump session lookup SQL if this regression returns.
        self.assertEqual(len(queries), 0)
        self.assertEqual(response.status_code, 200)

    def test_authenticated_health_probes_survive_database_access_loss(self):
        user = User.objects.create_user("health@example.invalid", full_name="Conta sintética")
        self.client.force_login(user)
        with patch("config.views.connection.cursor", side_effect=OperationalError("private details")):
            live = self.client.get(reverse("health-live"))
            ready = self.client.get(reverse("health-ready"))
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json(), {"status": "ok"})
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json(), {"status": "unavailable"})

    def test_liveness_does_not_query_database(self):
        with self.assertNumQueries(0):
            response = self.client.get(reverse("health-live"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_readiness_queries_real_database(self):
        with self.assertNumQueries(1):
            response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_database_failure_returns_503_without_connection_details(self):
        with patch("config.views.connection.cursor", side_effect=OperationalError("private details")):
            response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "unavailable"})
        self.assertNotIn(b"private details", response.content)

    def test_health_routes_do_not_accept_writes(self):
        for name in ("health-live", "health-ready"):
            with self.subTest(name=name):
                self.assertEqual(self.client.post(reverse(name)).status_code, 405)
