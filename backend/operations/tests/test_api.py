import uuid
from unittest.mock import patch

from django.contrib.auth.models import Permission
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.api_protocol import request_data
from accounts.models import ParticipantProfile, User
from operations.models import AuditEntry
from registrations.models import Registration, RegistrationCategory, RegistrationPrice
from registrations.services import register
from registrations.tests import factories


class ManagementApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = factories.user("operator@example.org", is_staff=True)
        cls.owner = factories.user()
        cls.other = factories.user("other@example.org")
        cls.event = factories.event()
        cls.price = factories.price(cls.event, requires_review=True)
        cls.registration, _ = register(user=cls.owner, event_id=cls.event.pk, price_id=cls.price.pk)
        cls.second, _ = register(user=cls.other, event_id=cls.event.pk, price_id=cls.price.pk)
        cls.foreign_event = factories.event("other-event")
        cls.foreign_price = factories.price(cls.foreign_event, requires_review=True)
        cls.foreign, _ = register(user=cls.owner, event_id=cls.foreign_event.pk, price_id=cls.foreign_price.pk)
        ParticipantProfile.objects.create(user=cls.owner, institution="UFPA", country="BR", preferred_language="en")

    def setUp(self):
        self.client.force_login(self.actor)

    def url(self, name, obj=None):
        return reverse("management_api:" + name, kwargs={"registration_id": str(obj.pk)} if obj else None)

    def grant(self, *codes):
        self.actor.user_permissions.set(Permission.objects.filter(content_type__app_label="registrations", codename__in=codes))

    def post(self, name, data, obj=None, **extra):
        return self.client.post(self.url(name, obj or self.registration), data, content_type="application/json", **extra)

    def test_anonymous_and_inactive_sessions_return_json_without_redirects(self):
        anonymous = Client()
        for name, obj in (("session", None), ("registrations", None), ("registration", self.registration), ("indicators", None)):
            response = anonymous.get(self.url(name, obj))
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.json()["error"]["code"], "authentication_required")
            self.assertNotIn("Location", response)
        for name in ("review", "cancel"):
            self.assertEqual(anonymous.post(self.url(name, self.registration), {}, content_type="application/json").status_code, 401)
        User.objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.url("session")).status_code, 401)

    def test_participant_or_nonstaff_with_permissions_cannot_use_management(self):
        self.grant("view_registration", "view_registration_indicators", "review_registration", "cancel_registration")
        User.objects.filter(pk=self.actor.pk).update(is_staff=False)
        for name, obj in (("session", None), ("registrations", None), ("registration", self.registration), ("indicators", None)):
            self.assertEqual(self.client.get(self.url(name, obj)).status_code, 403)
        self.assertEqual(self.post("review", {"decision": "approved", "reason": "Teste"}).status_code, 403)
        self.assertEqual(self.post("cancel", {"reason": "Teste"}).status_code, 403)

    def test_staff_flag_alone_has_no_access_to_registrations_or_indicators(self):
        for name, obj in (("registrations", None), ("registration", self.registration), ("indicators", None)):
            self.assertEqual(self.client.get(self.url(name, obj)).status_code, 403)
        self.assertEqual(self.post("review", {"decision": "approved", "reason": "Teste"}).status_code, 403)
        self.assertEqual(self.post("cancel", {"reason": "Teste"}).status_code, 403)
        session = self.client.get(self.url("session")).json()["data"]
        self.assertFalse(any(session["permissions"].values()))

    def test_bootstrap_exposes_only_operator_capabilities_and_masked_csrf(self):
        self.grant("view_registration", "review_registration")
        # Technical staff sessions keep the existing admin eligibility rules.
        User.objects.filter(pk=self.actor.pk).update(email_verified_at=None)
        response = self.client.get(self.url("session"))
        data = response.json()["data"]
        self.assertEqual(data["operator"], {"id": str(self.actor.pk), "full_name": self.actor.full_name})
        self.assertEqual(len(data["csrf_token"]), 64)
        self.assertTrue(data["permissions"]["review_category"])
        self.assertFalse(data["permissions"]["cancel_registration"])
        self.assertFalse(data["permissions"]["view_indicators"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotContains(response, self.owner.email)
        self.assertNotContains(response, "password")
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_list_is_current_event_only_and_uses_explicit_safe_fields(self):
        self.grant("view_registration")
        response = self.client.get(self.url("registrations"))
        data = response.json()["data"]
        self.assertEqual(data["pagination"]["total"], 2)
        self.assertEqual({row["id"] for row in data["registrations"]}, {str(self.registration.pk), str(self.second.pk)})
        row = next(row for row in data["registrations"] if row["id"] == str(self.registration.pk))
        self.assertEqual(row["amount"], "123.45")
        self.assertEqual(row["participant"]["email"], self.owner.email)
        self.assertFalse(row["actions"]["can_review"])
        self.assertNotIn("institution", row["participant"])
        self.assertNotIn("cancellation_reason", row)
        self.assertNotContains(response, "password")
        self.assertNotContains(response, "is_superuser")
        self.assertNotContains(response, str(self.foreign.pk))
        self.assertIn("no-store", response["Cache-Control"])

    def test_search_by_email_name_and_uuid_is_case_insensitive_and_scoped(self):
        self.grant("view_registration")
        User.objects.filter(pk=self.owner.pk).update(full_name="Pessoa Distinta")
        for query in (self.owner.email.upper(), "distinta", str(self.registration.pk)):
            data = self.client.get(self.url("registrations"), {"q": query}).json()["data"]
            self.assertEqual([row["id"] for row in data["registrations"]], [str(self.registration.pk)])
        data = self.client.get(self.url("registrations"), {"q": str(self.foreign.pk)}).json()["data"]
        self.assertEqual(data["registrations"], [])

    def test_filters_status_and_review_combine(self):
        self.grant("view_registration")
        Registration.objects.filter(pk=self.second.pk).update(category_review="rejected")
        response = self.client.get(self.url("registrations"), {"status": "pending", "review": "rejected"})
        self.assertEqual([row["id"] for row in response.json()["data"]["registrations"]], [str(self.second.pk)])
        self.assertEqual(self.client.get(self.url("registrations"), {"status": "confirmed"}).json()["data"]["pagination"]["total"], 0)

    def test_pagination_is_stable_bounded_and_does_not_silently_clamp_pages(self):
        self.grant("view_registration")
        first = self.client.get(self.url("registrations"), {"page_size": "1"}).json()["data"]
        second = self.client.get(self.url("registrations"), {"page": "2", "page_size": "1"}).json()["data"]
        self.assertEqual(first["pagination"], {"page": 1, "page_size": 1, "total": 2, "pages": 2, "has_next": True, "has_previous": False})
        self.assertNotEqual(first["registrations"][0]["id"], second["registrations"][0]["id"])
        self.assertEqual(self.client.get(self.url("registrations"), {"page": "3", "page_size": "1"}).json()["error"]["code"], "page_not_found")
        for data in ({"page": "0"}, {"page": "bad"}, {"page_size": "0"}, {"page_size": "101"}, {"q": "x" * 256}, {"status": "bad"}, {"review": "bad"}):
            response = self.client.get(self.url("registrations"), data)
            self.assertEqual(response.status_code, 422)
            self.assertIn("fields", response.json()["error"])

    def test_query_unknown_or_duplicate_fields_are_rejected(self):
        self.grant("view_registration", "view_registration_indicators")
        for name, obj in (("session", None), ("registrations", None), ("registration", self.registration), ("indicators", None)):
            response = self.client.get(self.url(name, obj), {"event_id": str(self.foreign_event.pk)})
            self.assertEqual(response.json()["error"]["code"], "unknown_fields")
        response = self.client.get(self.url("registrations") + "?status=pending&status=cancelled")
        self.assertEqual(response.json()["error"]["code"], "invalid_payload")

    def test_detail_has_profile_and_historical_category_without_sensitive_account_fields(self):
        self.grant("view_registration", "review_registration")
        category = self.price.category
        category.name_pt = "Nome atual do catálogo"
        category.save(update_fields=["name_pt"])
        response = self.client.get(self.url("registration", self.registration))
        data = response.json()["data"]["registration"]
        self.assertEqual(data["category_names"]["pt"], self.registration.category_name)
        self.assertEqual(data["participant"]["institution"], "UFPA")
        self.assertEqual(data["participant"]["country"], "BR")
        self.assertTrue(data["actions"]["can_review"])
        self.assertIsNone(data["cancelled_by_id"])
        self.assertNotContains(response, "password")
        self.assertNotContains(response, "token")

    def test_foreign_event_unknown_and_malformed_ids_return_the_same_json_404(self):
        self.grant("view_registration", "review_registration", "cancel_registration")
        for identifier in (str(self.foreign.pk), str(uuid.uuid4()), "not-a-uuid"):
            for name, data in (("registration", None), ("review", {"decision": "approved", "reason": "Teste"}), ("cancel", {"reason": "Teste"})):
                url = reverse("management_api:" + name, kwargs={"registration_id": identifier})
                response = self.client.get(url) if data is None else self.client.post(url, data, content_type="application/json")
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json()["error"]["code"], "registration_not_found")
                self.assertIn("no-store", response["Cache-Control"])
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.status, "pending")

    def test_view_permission_does_not_allow_mutations_or_indicators(self):
        self.grant("view_registration")
        self.assertEqual(self.post("review", {"decision": "approved", "reason": "Teste"}).status_code, 403)
        self.assertEqual(self.post("cancel", {"reason": "Teste"}).status_code, 403)
        self.assertEqual(self.client.get(self.url("indicators")).status_code, 403)
        self.assertFalse(AuditEntry.objects.filter(action__in=["registration.category_reviewed", "registration.cancelled"]).exists())

    def test_review_and_cancel_each_require_view_plus_the_specific_permission(self):
        self.grant("review_registration", "cancel_registration")
        self.assertEqual(self.post("review", {"decision": "approved", "reason": "Teste"}).status_code, 403)
        self.assertEqual(self.post("cancel", {"reason": "Teste"}).status_code, 403)
        self.grant("view_registration", "review_registration")
        self.assertEqual(self.post("cancel", {"reason": "Teste"}).status_code, 403)
        self.grant("view_registration", "cancel_registration")
        self.assertEqual(self.post("review", {"decision": "approved", "reason": "Teste"}).status_code, 403)

    def test_review_reuses_audit_is_repeatable_and_never_confirms_payment(self):
        self.grant("view_registration", "review_registration")
        for _ in range(2):
            response = self.post("review", {"decision": "approved", "reason": " Conferido "})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["data"]["registration"]["status"], "pending")
        entry = AuditEntry.objects.get(action="registration.category_reviewed")
        self.assertEqual(entry.actor_id, self.actor.pk)
        self.assertEqual(entry.reason, "Conferido")
        self.assertEqual(entry.details, {"previous": "pending", "decision": "approved"})
        response = self.post("review", {"decision": "rejected", "reason": "Reavaliado"})
        self.assertEqual(response.json()["data"]["registration"]["category_review"], "rejected")
        self.assertEqual(AuditEntry.objects.filter(action="registration.category_reviewed").count(), 2)
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.amount, self.price.amount)

    def test_cancel_preserves_original_reason_actor_and_one_history_entry_on_repeat(self):
        self.grant("view_registration", "cancel_registration")
        response = self.post("cancel", {"reason": "Solicitação da pessoa"})
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]["registration"]
        self.assertEqual(data["cancelled_by_id"], str(self.actor.pk))
        self.assertFalse(data["actions"]["can_cancel"])
        repeat = self.post("cancel", {"reason": "Outro motivo"})
        self.assertEqual(repeat.json()["data"]["registration"]["cancellation_reason"], "Solicitação da pessoa")
        self.assertEqual(AuditEntry.objects.filter(action="registration.cancelled").count(), 1)

    def test_blank_long_missing_reasons_and_invalid_decisions_are_field_errors(self):
        self.grant("view_registration", "review_registration", "cancel_registration")
        for reason in ("", " \t ", "x" * 1001):
            self.assertEqual(self.post("review", {"decision": "approved", "reason": reason}).status_code, 422)
            self.assertEqual(self.post("cancel", {"reason": reason}).status_code, 422)
        self.assertEqual(self.post("cancel", {}).status_code, 422)
        self.assertEqual(self.post("review", {"decision": "confirmed", "reason": "Teste"}).status_code, 422)
        self.assertFalse(AuditEntry.objects.filter(action__in=["registration.category_reviewed", "registration.cancelled"]).exists())

    def test_ownership_payment_or_snapshot_injection_is_rejected(self):
        self.grant("view_registration", "review_registration", "cancel_registration")
        for name, base in (("review", {"decision": "approved", "reason": "Teste"}), ("cancel", {"reason": "Teste"})):
            for field in ("user", "actor", "event_id", "amount", "status", "currency", "category_id"):
                response = self.post(name, {**base, field: "injected"})
                self.assertEqual(response.json()["error"]["code"], "unknown_fields")
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "pending")
        self.assertEqual(self.registration.category_review, "pending")

    def test_review_of_nonreviewable_or_cancelled_registration_is_conflict(self):
        self.grant("view_registration", "review_registration", "cancel_registration")
        category = RegistrationCategory.objects.create(event=self.event, code="ordinary", name_pt="Sem revisão")
        price = RegistrationPrice.objects.create(category=category, amount=self.price.amount,
                                                 currency="BRL", valid_from=self.price.valid_from)
        owner = factories.user("ordinary@example.org")
        ordinary, _ = register(user=owner, event_id=self.event.pk, price_id=price.pk)
        response = self.post("review", {"decision": "approved", "reason": "Teste"}, obj=ordinary)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "registration_action_unavailable")
        self.post("cancel", {"reason": "Teste"})
        self.assertEqual(self.post("review", {"decision": "approved", "reason": "Teste"}).status_code, 409)

    def test_audit_database_failure_rolls_back_review_and_cancel(self):
        self.grant("view_registration", "review_registration", "cancel_registration")
        for name, data in (("review", {"decision": "approved", "reason": "Teste"}), ("cancel", {"reason": "Teste"})):
            with patch("registrations.services.audit", side_effect=DatabaseError("Synthetic failure")):
                response = self.post(name, data)
            self.assertEqual(response.status_code, 503)
            self.assertEqual(response.json()["error"]["code"], "operation_unavailable")
            self.registration.refresh_from_db()
            self.assertEqual(self.registration.status, "pending")
            self.assertEqual(self.registration.category_review, "pending")
            self.assertIsNone(self.registration.cancelled_at)

    def test_permission_revocation_during_request_is_rechecked_before_write(self):
        self.grant("view_registration", "cancel_registration")
        def revoke(request):
            self.actor.user_permissions.clear()
            return request_data(request)
        with patch("operations.api.request_data", side_effect=revoke):
            self.assertEqual(self.post("cancel", {"reason": "Teste"}).status_code, 403)
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "pending")

    def test_account_deactivation_during_request_is_rechecked_before_write(self):
        self.grant("view_registration", "cancel_registration")
        def deactivate(request):
            User.objects.filter(pk=self.actor.pk).update(is_active=False)
            return request_data(request)
        with patch("operations.api.request_data", side_effect=deactivate):
            self.assertEqual(self.post("cancel", {"reason": "Teste"}).status_code, 401)
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "pending")

    def test_csrf_and_cross_origin_are_enforced_with_json_errors(self):
        self.grant("view_registration", "cancel_registration")
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        token = client.get(self.url("session")).json()["data"]["csrf_token"]
        url = self.url("cancel", self.registration)
        for headers in ({}, {"HTTP_X_CSRFTOKEN": token, "HTTP_ORIGIN": "https://foreign.invalid"}):
            response = client.post(url, {"reason": "Teste"}, content_type="application/json", **headers)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "csrf_failed")
            self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(client.post(url, {"reason": "Teste"}, content_type="application/json", HTTP_X_CSRFTOKEN=token).status_code, 200)

    def test_payload_parser_rejects_duplicates_types_malformed_and_oversize(self):
        self.grant("view_registration", "cancel_registration")
        for raw in ('{"reason":"one","reason":"two"}', '{"reason":null}', '{"reason":123}', '[]', '{broken'):
            response = self.client.post(self.url("cancel", self.registration), raw, content_type="application/json")
            self.assertEqual(response.json()["error"]["code"], "invalid_payload")
        response = self.post("cancel", {"reason": "x" * (33 * 1024)})
        self.assertEqual(response.status_code, 413)
        response = self.client.post(self.url("cancel", self.registration), "reason=test", content_type="text/plain")
        self.assertEqual(response.status_code, 415)

    def test_urlencoded_actions_are_supported_without_allowing_repeated_fields(self):
        self.grant("view_registration", "review_registration")
        response = self.client.post(self.url("review", self.registration), {"decision": "approved", "reason": "Teste"})
        self.assertEqual(response.status_code, 415)  # Django test default multipart is deliberately unsupported.
        response = self.client.post(self.url("review", self.registration), "decision=approved&reason=Teste", content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.status_code, 200)
        response = self.client.post(self.url("review", self.registration), "decision=approved&reason=one&reason=two", content_type="application/x-www-form-urlencoded")
        self.assertEqual(response.json()["error"]["code"], "invalid_payload")

    def test_methods_are_explicit_and_get_never_mutates(self):
        self.grant("view_registration", "review_registration", "cancel_registration", "view_registration_indicators")
        for name in ("review", "cancel"):
            response = self.client.get(self.url(name, self.registration))
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Allow"], "POST")
        for name, obj in (("session", None), ("registrations", None), ("registration", self.registration), ("indicators", None)):
            response = self.client.post(self.url(name, obj), {}, content_type="application/json")
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Allow"], "GET")

    def test_indicator_only_role_sees_aggregates_without_participant_data(self):
        self.grant("view_registration_indicators")
        response = self.client.get(self.url("indicators"))
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertEqual(data["total"], 2)
        self.assertEqual(data["amounts"], [{"currency": "BRL", "count": 2, "total": "246.90"}])
        self.assertEqual(data["amount_basis"], "registration_value_excluding_cancelled")
        self.assertNotContains(response, self.owner.email)
        self.assertNotContains(response, str(self.registration.pk))
        self.assertNotContains(response, "UFPA")
        self.assertEqual(self.client.get(self.url("registrations")).status_code, 403)

    def test_indicators_count_cancelled_but_exclude_their_amounts(self):
        self.grant("view_registration", "cancel_registration", "view_registration_indicators")
        self.post("cancel", {"reason": "Teste"})
        data = self.client.get(self.url("indicators")).json()["data"]
        self.assertEqual(data["total"], 2)
        self.assertEqual({row["code"]: row["total"] for row in data["by_status"]}, {"pending": 1, "confirmed": 0, "cancelled": 1})
        self.assertEqual(data["amounts"], [{"currency": "BRL", "count": 1, "total": "123.45"}])

    def test_indicators_keep_currencies_separate(self):
        self.grant("view_registration_indicators")
        self.event.registration_currency = "USD"
        self.event.save(update_fields=["registration_currency"])
        price = RegistrationPrice.objects.create(category=self.price.category, amount="9.00", currency="USD", valid_from=self.price.valid_from)
        owner = factories.user("usd@example.org")
        register(user=owner, event_id=self.event.pk, price_id=price.pk)
        data = self.client.get(self.url("indicators")).json()["data"]
        self.assertEqual(data["amounts"], [{"currency": "BRL", "count": 2, "total": "246.90"},
                                          {"currency": "USD", "count": 1, "total": "9.00"}])

    def test_empty_current_event_has_stable_list_and_indicator_shapes(self):
        self.grant("view_registration", "view_registration_indicators")
        factories.event("empty-event")
        with override_settings(REGISTRATION_EVENT_CODE="empty-event"):
            data = self.client.get(self.url("registrations")).json()["data"]
            self.assertEqual(data["registrations"], [])
            self.assertEqual(data["pagination"]["total"], 0)
            self.assertEqual(data["pagination"]["pages"], 1)
            data = self.client.get(self.url("indicators")).json()["data"]
            self.assertEqual(data["total"], 0)
            self.assertEqual(data["amounts"], [])
            self.assertFalse(any(row["total"] for row in data["by_status"] + data["by_review"]))

    @override_settings(REGISTRATION_EVENT_CODE="not-configured")
    def test_no_event_returns_json_404_but_session_remains_available(self):
        self.grant("view_registration", "review_registration", "cancel_registration", "view_registration_indicators")
        for name, obj in (("registrations", None), ("registration", self.registration), ("indicators", None)):
            response = self.client.get(self.url(name, obj))
            self.assertEqual(response.json()["error"]["code"], "event_not_configured")
        for name, data in (("review", {"decision": "approved", "reason": "Teste"}), ("cancel", {"reason": "Teste"})):
            self.assertEqual(self.post(name, data).json()["error"]["code"], "event_not_configured")
        self.assertEqual(self.client.get(self.url("session")).status_code, 200)
