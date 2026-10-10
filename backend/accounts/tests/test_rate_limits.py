from datetime import timedelta

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.middleware import client_ip
from accounts.models import AuthThrottleBucket

SMALL_LIMITS = {
    "login": {"ip": (20, 60), "identity": (2, 60)},
    "signup": {"ip": (2, 60), "identity": (2, 60)},
    "request_email": {"ip": (10, 60), "identity": (2, 60)},
    "confirm": {"ip": (2, 60)},
    "reset": {"ip": (2, 60)},
}


@override_settings(ACCOUNT_RATE_LIMITS=SMALL_LIMITS)
class RateLimitTests(TestCase):
    def test_identity_limit_is_shared_by_admin_and_participant_and_cannot_be_spoofed(self):
        routes = ("accounts:login", "admin:login", "accounts:login")
        for index, route in enumerate(routes):
            response = self.client.post(reverse(route), {
                "username": "TARGET@EXAMPLE.ORG", "email": f"decoy-{index}@example.org", "password": "invalid",
            }, REMOTE_ADDR=f"203.0.113.{index+1}")
            self.assertEqual(response.status_code, 429 if index == 2 else 200)
        self.assertGreater(int(response["Retry-After"]), 0)
        self.assertIn("no-store", response["Cache-Control"])
        for key in AuthThrottleBucket.objects.values_list("key", flat=True):
            self.assertEqual(len(key), 64)
            self.assertNotIn("target", key)
            self.assertNotIn("203.0.113", key)

    def test_ip_limit_prevents_signup_by_rotating_addresses(self):
        for index in range(3):
            response = self.client.post(reverse("accounts:signup"), {"email": f"different-{index}@example.org"})
            self.assertEqual(response.status_code, 429 if index == 2 else 200)

    def test_forged_forwarded_header_does_not_bypass_default_limit(self):
        for index in range(3):
            response = self.client.post(reverse("accounts:confirm"), {"token": "invalid"}, HTTP_X_FORWARDED_FOR=f"203.0.113.{index+1}")
            self.assertEqual(response.status_code, 429 if index == 2 else 200)

    def test_expired_windows_allow_new_attempts(self):
        payload = {"username": "missing@example.org", "password": "invalid"}
        self.client.post(reverse("accounts:login"), payload)
        self.client.post(reverse("accounts:login"), payload)
        self.assertEqual(self.client.post(reverse("accounts:login"), payload).status_code, 429)
        AuthThrottleBucket.objects.update(window_start=timezone.now() - timedelta(seconds=61))
        self.assertEqual(self.client.post(reverse("accounts:login"), payload).status_code, 200)

    def test_email_request_limit_does_not_depend_on_account_existence(self):
        for index in range(3):
            response = self.client.post(reverse("accounts:request_reset"), {"email": "missing@example.org"}, REMOTE_ADDR=f"203.0.113.{index+1}")
            self.assertEqual(response.status_code, 429 if index == 2 else 302)

    @override_settings(AUTH_TRUSTED_PROXY_NETWORKS=["10.0.0.0/8"])
    def test_proxy_chain_uses_first_untrusted_address_from_the_right(self):
        factory = RequestFactory()
        request = factory.get("/", REMOTE_ADDR="10.0.0.2", HTTP_X_FORWARDED_FOR="192.0.2.99, 203.0.113.7, 10.0.0.1")
        self.assertEqual(client_ip(request), "203.0.113.7")
        untrusted = factory.get("/", REMOTE_ADDR="198.51.100.2", HTTP_X_FORWARDED_FOR="192.0.2.99")
        self.assertEqual(client_ip(untrusted), "198.51.100.2")
        malformed = factory.get("/", REMOTE_ADDR="10.0.0.2", HTTP_X_FORWARDED_FOR="invalid-address")
        self.assertEqual(client_ip(malformed), "10.0.0.2")
