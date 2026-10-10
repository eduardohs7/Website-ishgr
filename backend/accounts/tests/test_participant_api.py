from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.api_protocol import request_data
from accounts.models import AccountEmail, ParticipantProfile, User

PASSWORD = "Synthetic-participant-password-47!"


class ParticipantProfileApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = User.objects.create_user(
            "owner@example.org", PASSWORD, full_name="Pessoa Principal", email_verified_at=timezone.now(),
        )
        cls.other = User.objects.create_user(
            "other@example.org", PASSWORD, full_name="Outra Pessoa", email_verified_at=timezone.now(),
        )
        cls.pending = User.objects.create_user("pending@example.org", PASSWORD, full_name="Pessoa Pendente")
        ParticipantProfile.objects.create(user=cls.owner, institution="Original", country="BR", preferred_language="es")
        ParticipantProfile.objects.create(user=cls.other, institution="Outra Instituição", country="PT", preferred_language="en")

    def setUp(self):
        self.client.force_login(self.owner)

    def post(self, payload, client=None, **extra):
        return (client or self.client).post(reverse("participant_api:profile"), payload, content_type="application/json", **extra)

    def payload(self, **extra):
        return {"full_name": "Nome Atualizado", "institution": "UFPA", "country": "PT", "preferred_language": "en", **extra}

    def test_all_participant_routes_require_active_verified_session(self):
        routes = ("profile", "profile_options", "event", "prices", "registration")
        client = Client()
        for route in routes:
            response = client.get(reverse(f"participant_api:{route}"))
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["error"]["code"], "authentication_required")
        client.force_login(self.pending)
        for route in routes:
            response = client.get(reverse(f"participant_api:{route}"))
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "email_not_verified")
        self.owner.is_active = False
        self.owner.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(reverse("participant_api:profile")).status_code, 401)

    def test_get_profile_only_returns_owner_and_ignores_targeting_query(self):
        response = self.client.get(reverse("participant_api:profile"), {"user": str(self.other.pk)})
        profile = response.json()["data"]["profile"]
        self.assertEqual(profile["id"], str(self.owner.pk))
        self.assertEqual(profile["email"], self.owner.email)
        self.assertEqual(set(profile), {"id", "full_name", "email", "institution", "country", "preferred_language"})
        self.assertNotContains(response, self.other.email)
        self.assertEqual(response["Content-Language"], "es")
        self.assertIn("no-store", response["Cache-Control"])

    def test_profile_update_changes_only_owner_and_returns_new_language(self):
        response = self.post(self.payload())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Language"], "en")
        profile = response.json()["data"]["profile"]
        self.assertEqual(profile["full_name"], "Nome Atualizado")
        self.assertEqual(profile["institution"], "UFPA")
        self.assertEqual(profile["country"], "PT")
        self.assertEqual(profile["preferred_language"], "en")
        self.owner.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.owner.email, "owner@example.org")
        self.assertTrue(self.owner.check_password(PASSWORD))
        self.assertFalse(self.owner.is_staff)
        self.assertEqual(self.other.full_name, "Outra Pessoa")
        self.assertEqual(self.other.profile.institution, "Outra Instituição")
        next_response = self.client.get(reverse("participant_api:profile_options"), HTTP_ACCEPT_LANGUAGE="es")
        self.assertEqual(next_response["Content-Language"], "en")
        self.client.post(reverse("auth_api:request_reset"), {"email": self.owner.email}, content_type="application/json")
        self.assertEqual(AccountEmail.objects.get(token__user=self.owner).language, "en")

    def test_profile_rejects_owner_email_and_privilege_injection(self):
        for field, value in {"user": str(self.other.pk), "id": str(self.other.pk), "email": "injected@example.org", "is_staff": "true", "password": "injected"}.items():
            response = self.post(self.payload(**{field: value}))
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "unknown_fields")
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.full_name, "Pessoa Principal")
        self.assertEqual(self.owner.profile.institution, "Original")

    def test_invalid_profile_is_not_partially_saved(self):
        for extra, field in [({"country": "ZZ"}, "country"), ({"preferred_language": "fr"}, "preferred_language"), ({"full_name": " "}, "full_name"), ({"institution": "x" * 256}, "institution")]:
            response = self.post(self.payload(**extra))
            self.assertEqual(response.status_code, 422)
            self.assertIn(field, response.json()["error"]["fields"])
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.full_name, "Pessoa Principal")
        self.assertEqual(self.owner.profile.institution, "Original")
        self.assertEqual(self.owner.profile.preferred_language, "es")

    def test_profile_is_full_update_and_optional_fields_can_be_cleared(self):
        response = self.post({"full_name": "Pessoa Principal", "preferred_language": "es"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["profile"]["institution"], "")
        self.assertEqual(response.json()["data"]["profile"]["country"], "")
        self.assertEqual(self.post({"institution": "UFPA"}).status_code, 422)

    def test_missing_profile_has_safe_defaults_and_can_be_created(self):
        ParticipantProfile.objects.filter(user=self.owner).delete()
        response = self.client.get(reverse("participant_api:profile"))
        self.assertEqual(response.json()["data"]["profile"]["preferred_language"], "pt-br")
        self.assertEqual(response.json()["data"]["profile"]["institution"], "")
        self.assertFalse(ParticipantProfile.objects.filter(user=self.owner).exists())
        self.assertEqual(self.post(self.payload()).status_code, 200)
        self.assertEqual(ParticipantProfile.objects.filter(user=self.owner).count(), 1)

    def test_country_options_are_localized_and_use_supported_codes(self):
        response = self.client.get(reverse("participant_api:profile_options"), HTTP_ACCEPT_LANGUAGE="en")
        options = response.json()["data"]
        self.assertEqual(response["Content-Language"], "es")
        self.assertEqual([item["code"] for item in options["languages"]], ["pt-br", "en", "es"])
        countries = {item["code"]: item["name"] for item in options["countries"]}
        self.assertEqual(countries["BR"], "Brasil")
        self.assertIn("US", countries)
        self.assertNotIn("ZZ", countries)

    def test_mutations_require_csrf_and_cross_origin_requests_fail(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        response = self.post(self.payload(), client=client)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "csrf_failed")
        token = client.get(reverse("auth_api:session")).json()["data"]["csrf_token"]
        response = self.post(self.payload(), client=client, HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="https://evil.example")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.post(self.payload(), client=client, HTTP_X_CSRFTOKEN=token).status_code, 200)

    def test_profile_rechecks_revoked_verification_before_writing(self):
        def revoke(request):
            data = request_data(request)
            User.objects.filter(pk=self.owner.pk).update(email_verified_at=None)
            return data
        with patch("accounts.participant_api.request_data", side_effect=revoke):
            response = self.post(self.payload())
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "email_not_verified")
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.full_name, "Pessoa Principal")
        self.assertEqual(self.owner.profile.institution, "Original")

    def test_methods_and_payload_follow_the_auth_api_contract(self):
        response = self.client.patch(reverse("participant_api:profile"), {}, content_type="application/json")
        self.assertEqual(response.status_code, 405)
        self.assertEqual(response["Allow"], "GET, POST")
        self.assertIn("no-store", response["Cache-Control"])
        response = self.client.post(reverse("participant_api:profile"), '{"country":"BR","country":"PT"}', content_type="application/json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_payload")
        response = self.client.post(reverse("participant_api:profile"), "text", content_type="text/plain")
        self.assertEqual(response.status_code, 415)
