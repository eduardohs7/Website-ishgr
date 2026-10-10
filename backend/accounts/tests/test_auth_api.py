import json
from datetime import timedelta

from django.conf import settings
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountActionToken, AccountEmail, AuthThrottleBucket, ParticipantProfile, User
from accounts.services import request_account_email

PASSWORD = "Synthetic-participant-password-47!"
NEW_PASSWORD = "Synthetic-replacement-password-92!"


class AuthApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.confirmed = User.objects.create_user(
            "confirmed@example.org", PASSWORD, full_name="Pessoa Confirmada", email_verified_at=timezone.now(),
        )
        cls.unconfirmed = User.objects.create_user("pending@example.org", PASSWORD, full_name="Pessoa Pendente")
        ParticipantProfile.objects.create(user=cls.confirmed, preferred_language="es")

    def post(self, route, payload, client=None, **extra):
        return (client or self.client).post(reverse(f"auth_api:{route}"), payload, content_type="application/json", **extra)

    def signup_payload(self, **extra):
        return {
            "first_name": "Maria", "last_name": "da Silva", "email": "new@example.org",
            "password1": PASSWORD, "password2": PASSWORD, "institution": "UFPA", "country": "BR", "language": "en",
            **extra,
        }

    def token(self, user, purpose):
        request_account_email(user.email, purpose)
        return AccountEmail.objects.get(token__user=user, token__purpose=purpose, status="pending").raw_token

    def test_signup_confirmation_login_logout_session_end_to_end(self):
        response = self.post("signup", self.signup_payload(email=" NEW+Event@EXAMPLE.ORG "))
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"ok": True, "data": {"code": "signup_received"}})
        user = User.objects.get(email="new+event@example.org")
        self.assertEqual(user.full_name, "Maria da Silva")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertIsNone(user.email_verified_at)
        self.assertEqual(user.profile.institution, "UFPA")
        self.assertEqual(str(user.profile.country), "BR")
        self.assertEqual(user.profile.preferred_language, "en")
        delivery = AccountEmail.objects.get(token__user=user)
        self.assertEqual(delivery.language, "en")
        self.assertEqual(self.post("login", {"email": user.email, "password": PASSWORD}).status_code, 401)
        self.assertEqual(self.post("confirm", {"token": delivery.raw_token}).status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        response = self.post("login", {"email": user.email.upper(), "password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["participant"]["id"], str(user.pk))
        self.assertEqual(response.json()["data"]["next"], "/participante/")
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))
        self.assertEqual(self.client.get(reverse("auth_api:session")).json()["data"]["authenticated"], True)
        self.assertEqual(self.post("logout", {}).status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.get(reverse("auth_api:session")).json()["data"]["authenticated"], False)

    def test_session_bootstraps_csrf_and_exposes_only_current_verified_user(self):
        url = reverse("auth_api:session")
        response = self.client.get(url)
        self.assertFalse(response.json()["data"]["authenticated"])
        self.assertIsNone(response.json()["data"]["participant"])
        self.assertTrue(response.json()["data"]["csrf_token"])
        self.assertIn(settings.CSRF_COOKIE_NAME, response.cookies)
        self.assertIn("no-store", response["Cache-Control"])
        self.client.force_login(self.unconfirmed)
        self.assertIsNone(self.client.get(url).json()["data"]["participant"])
        self.client.force_login(self.confirmed)
        response = self.client.get(url)
        self.assertEqual(set(response.json()["data"]["participant"]), {"id", "email", "full_name"})
        self.assertEqual(response["Content-Language"], "es")

    def test_duplicate_signup_same_response_without_overwriting_profile_or_password(self):
        first = self.post("signup", self.signup_payload())
        before = self.confirmed.password
        duplicate = self.post("signup", self.signup_payload(email=self.confirmed.email.upper()))
        self.assertEqual(first.status_code, duplicate.status_code)
        self.assertEqual(first.json(), duplicate.json())
        self.confirmed.refresh_from_db()
        self.assertEqual(self.confirmed.password, before)
        self.assertEqual(self.confirmed.full_name, "Pessoa Confirmada")
        self.assertEqual(self.confirmed.profile.preferred_language, "es")
        self.assertFalse(AccountEmail.objects.filter(token__user=self.confirmed).exists())

    def test_signup_rejects_privilege_injection(self):
        response = self.post("signup", self.signup_payload(is_staff="true", email_verified_at="2026-01-01"))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "unknown_fields")
        self.assertFalse(User.objects.filter(email="new@example.org").exists())

    def test_signup_validates_names_password_profile_and_language(self):
        cases = [
            ({"first_name": " "}, "first_name"), ({"last_name": " "}, "last_name"),
            ({"first_name": "a" * 128}, "first_name"),
            ({"password1": "123", "password2": "123"}, "password1"),
            ({"password2": NEW_PASSWORD}, "password2"),
            ({"country": "ZZ"}, "country"), ({"language": "fr"}, "language"),
            ({"institution": "a" * 256}, "institution"), ({"email": "invalid"}, "email"),
        ]
        # Avoid testing the limiter instead of each validation case.
        for extra, field in cases:
            AuthThrottleBucket.objects.all().delete()
            response = self.post("signup", self.signup_payload(**extra))
            self.assertEqual(response.status_code, 422)
            self.assertIn(field, response.json()["error"]["fields"])
            self.assertIn("code", response.json()["error"]["fields"][field][0])
        self.assertFalse(User.objects.filter(email="new@example.org").exists())
        self.assertEqual(AccountEmail.objects.count(), 0)

    def test_signup_without_optional_profile_fields_uses_request_language(self):
        data = self.signup_payload()
        for field in ("country", "institution", "language"):
            data.pop(field)
        response = self.post("signup", data, HTTP_ACCEPT_LANGUAGE="es")
        self.assertEqual(response.status_code, 202)
        user = User.objects.get(email="new@example.org")
        self.assertEqual(user.profile.preferred_language, "es")
        self.assertEqual(user.profile.institution, "")
        self.assertEqual(str(user.profile.country), "")
        self.assertEqual(AccountEmail.objects.get(token__user=user).language, "es")

    def test_bad_credentials_inactive_and_unverified_are_indistinguishable(self):
        self.confirmed.is_active = False
        self.confirmed.save(update_fields=["is_active"])
        bodies = []
        for email in (self.confirmed.email, self.unconfirmed.email, "missing@example.org"):
            response = self.post("login", {"email": email, "password": PASSWORD})
            self.assertEqual(response.status_code, 401)
            bodies.append(response.json())
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[1], bodies[2])
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_rotates_session_and_csrf_and_honors_remember_me(self):
        self.client.get(reverse("auth_api:session"))
        self.client.session.save()
        old_session = self.client.session.session_key
        old_csrf = self.client.cookies[settings.CSRF_COOKIE_NAME].value
        response = self.post("login", {"email": self.confirmed.email, "password": PASSWORD})
        self.assertNotEqual(self.client.session.session_key, old_session)
        self.assertNotEqual(self.client.cookies[settings.CSRF_COOKIE_NAME].value, old_csrf)
        self.assertTrue(self.client.session.get_expire_at_browser_close())
        self.assertEqual(response.cookies[settings.SESSION_COOKIE_NAME]["expires"], "")
        self.post("logout", {})
        self.post("login", {"email": self.confirmed.email, "password": PASSWORD, "remember_me": "true"})
        self.assertFalse(self.client.session.get_expire_at_browser_close())
        self.assertLessEqual(self.client.session.get_expiry_age(), settings.SESSION_COOKIE_AGE)

    def test_login_next_is_validated_and_remember_me_is_strict(self):
        for target in ("https://evil.example/", "//evil.example/", "\\\\evil.example/", "javascript:alert(1)"):
            response = self.post("login", {"email": self.confirmed.email, "password": PASSWORD, "next": target})
            self.assertEqual(response.json()["data"]["next"], "/participante/")
        response = self.post("login", {"email": self.confirmed.email, "password": PASSWORD, "next": "/participante/perfil/"})
        self.assertEqual(response.json()["data"]["next"], "/participante/perfil/")
        self.assertEqual(self.post("login", {"email": self.confirmed.email, "password": PASSWORD, "remember_me": "forever"}).status_code, 422)

    def test_confirmation_is_post_only_single_use_and_bound_to_purpose(self):
        token = self.token(self.unconfirmed, "confirm")
        self.assertEqual(self.client.get(reverse("auth_api:confirm"), {"token": token}).status_code, 405)
        self.unconfirmed.refresh_from_db()
        self.assertIsNone(self.unconfirmed.email_verified_at)
        reset_token = self.token(self.confirmed, "reset")
        self.assertEqual(self.post("confirm", {"token": reset_token}).status_code, 400)
        self.assertEqual(self.post("confirm", {"token": token}).status_code, 200)
        self.assertEqual(self.post("confirm", {"token": token}).json()["error"]["code"], "invalid_token")

    def test_resend_revokes_previous_token(self):
        first = self.token(self.unconfirmed, "confirm")
        response = self.post("resend_confirmation", {"email": self.unconfirmed.email})
        self.assertEqual(response.status_code, 202)
        self.assertEqual(self.post("confirm", {"token": first}).status_code, 400)
        second = AccountEmail.objects.get(status="pending").raw_token
        self.assertEqual(self.post("confirm", {"token": second}).status_code, 200)

    def test_reset_requests_do_not_reveal_accounts(self):
        bodies = []
        for email in (self.confirmed.email, self.unconfirmed.email, "missing@example.org"):
            response = self.post("request_reset", {"email": email})
            self.assertEqual(response.status_code, 202)
            bodies.append(response.json())
        self.assertEqual(bodies[0], bodies[1])
        self.assertEqual(bodies[1], bodies[2])
        self.assertEqual(AccountEmail.objects.count(), 1)
        self.assertEqual(AccountEmail.objects.get().token.user_id, self.confirmed.pk)

    def test_reset_invalidates_sessions_and_cannot_reuse_token(self):
        other = Client()
        other.force_login(self.confirmed)
        raw = self.token(self.confirmed, "reset")
        response = self.post("reset", {"token": raw, "password1": NEW_PASSWORD, "password2": NEW_PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.confirmed.refresh_from_db()
        self.assertTrue(self.confirmed.check_password(NEW_PASSWORD))
        self.assertFalse(other.get(reverse("auth_api:session")).json()["data"]["authenticated"])
        response = self.post("reset", {"token": raw, "password1": PASSWORD, "password2": PASSWORD})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_token")

    def test_reset_rejects_weak_password_without_consuming_token(self):
        raw = self.token(self.confirmed, "reset")
        response = self.post("reset", {"token": raw, "password1": "123", "password2": "123"})
        self.assertEqual(response.status_code, 422)
        self.assertIn("password2", response.json()["error"]["fields"])
        self.assertNotIn("new_password1", response.json()["error"]["fields"])
        self.assertTrue(AccountActionToken.objects.filter(used_at__isnull=True).exists())

    def test_expired_token_is_rejected(self):
        raw = self.token(self.unconfirmed, "confirm")
        AccountActionToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.post("confirm", {"token": raw}).json()["error"]["code"], "invalid_token")

    def test_mutating_api_requires_csrf_and_json_errors_have_no_internal_reason(self):
        client = Client(enforce_csrf_checks=True)
        for route in ("signup", "login", "logout", "confirm", "resend_confirmation", "request_reset", "reset"):
            response = self.post(route, {}, client=client)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json(), {"ok": False, "error": {"code": "csrf_failed"}})
            self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(client.post(reverse("accounts:signup"), {}).status_code, 403)

    def test_csrf_bootstrap_login_rotation_and_cross_origin_rejection(self):
        client = Client(enforce_csrf_checks=True)
        first_token = client.get(reverse("auth_api:session")).json()["data"]["csrf_token"]
        response = self.post("login", {"email": self.confirmed.email, "password": PASSWORD}, client=client, HTTP_X_CSRFTOKEN=first_token)
        self.assertEqual(response.status_code, 200)
        rotated = response.json()["data"]["csrf_token"]
        self.assertEqual(self.post("logout", {}, client=client, HTTP_X_CSRFTOKEN=first_token).status_code, 403)
        self.assertEqual(self.post("logout", {}, client=client, HTTP_X_CSRFTOKEN=rotated, HTTP_ORIGIN="https://evil.example").status_code, 403)
        self.assertEqual(self.post("logout", {}, client=client, HTTP_X_CSRFTOKEN=rotated).status_code, 200)

    def test_urlencoded_payload_is_supported_with_csrf_field(self):
        client = Client(enforce_csrf_checks=True)
        token = client.get(reverse("auth_api:session")).json()["data"]["csrf_token"]
        from urllib.parse import urlencode
        response = client.post(reverse("auth_api:login"), urlencode({
            "email": self.confirmed.email, "password": PASSWORD, "csrfmiddlewaretoken": token,
        }), content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.status_code, 200)

    def test_malformed_body_duplicate_fields_and_wrong_types_are_rejected(self):
        url = reverse("auth_api:login")
        for payload in ("not-json", "[]", '"string"', '{"email":"a","email":"b"}', '{"email":null}', '{"password":123}', '{"email":{}}'):
            response = self.client.post(url, payload, content_type="application/json")
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "invalid_payload")
        response = self.client.post(url, "email=a&email=b", content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.status_code, 400)
        response = self.client.post(url, "email=x", content_type="text/plain")
        self.assertEqual(response.status_code, 415)
        response = self.client.post(url, json.dumps({"email": "a" * 32768}), content_type="application/json")
        self.assertEqual(response.status_code, 413)

    def test_all_api_responses_are_json_without_cache_and_routes_enforce_methods(self):
        for route in ("signup", "login", "logout", "confirm", "resend_confirmation", "request_reset", "reset"):
            response = self.client.get(reverse(f"auth_api:{route}"))
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Allow"], "POST")
            self.assertEqual(response["Content-Type"], "application/json")
            self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.post("session", {}).status_code, 405)

    @override_settings(ACCOUNT_RATE_LIMITS={"login": {"ip": (20, 60), "identity": (2, 60)}})
    def test_login_rate_limit_is_shared_with_html_and_admin_even_with_padded_email(self):
        self.client.post(reverse("accounts:login"), {"username": "TARGET@EXAMPLE.ORG", "password": "invalid"}, REMOTE_ADDR="203.0.113.1")
        self.client.post(reverse("admin:login"), {"username": "target@example.org", "password": "invalid"}, REMOTE_ADDR="203.0.113.2")
        response = self.post("login", {"email": " " * 300 + "Target@Example.org ", "password": "invalid"}, REMOTE_ADDR="203.0.113.3")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "rate_limited")
        self.assertGreater(int(response["Retry-After"]), 0)
        self.assertIn("no-store", response["Cache-Control"])

    @override_settings(ACCOUNT_RATE_LIMITS={"signup": {"ip": (2, 60), "identity": (20, 60)}})
    def test_signup_ip_limit_counts_api_and_html_attempts_together(self):
        self.post("signup", {"email": "a@example.org"})
        self.client.post(reverse("accounts:signup"), {"email": "b@example.org"})
        response = self.post("signup", {"email": "c@example.org"}, HTTP_X_FORWARDED_FOR="203.0.113.9")
        self.assertEqual(response.status_code, 429)

    @override_settings(ACCOUNT_RATE_LIMITS={"request_email": {"ip": (20, 60), "identity": (2, 60)}})
    def test_email_request_limits_are_shared_between_api_endpoints(self):
        self.post("request_reset", {"email": "missing@example.org"})
        self.post("resend_confirmation", {"email": "MISSING@example.org"})
        response = self.post("request_reset", {"email": "missing@example.org"})
        self.assertEqual(response.status_code, 429)
