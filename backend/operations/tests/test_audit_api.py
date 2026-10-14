from datetime import datetime, timedelta, timezone as datetime_timezone
import uuid
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
import pyotp

from accounts import mfa
from accounts.models import StaffMFA, User
from accounts.tests.test_mfa import TEST_SECRET
from operations.models import AuditEntry
from registrations.services import cancel_registration, register, review_category
from registrations.tests import factories


@override_settings(STAFF_MFA_REQUIRED=False)
class AuditApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = factories.user("auditor@example.org", is_staff=True)
        cls.owner = factories.user()
        cls.other = factories.user("other@example.org", is_staff=True)
        cls.permission = Permission.objects.get(content_type__app_label="operations", codename="view_auditentry")
        cls.event = factories.event()
        cls.price = factories.price(cls.event, requires_review=True)
        cls.registration, _ = register(user=cls.owner, event_id=cls.event.pk, price_id=cls.price.pk)
        cls.created = AuditEntry.objects.get(action="registration.created")
        cls.reviewed = AuditEntry.objects.create(actor=cls.actor, action="registration.category_reviewed",
            target_type="registrations.registration", target_id=str(cls.registration.pk),
            reason="Documentação verificada pela equipe.", details={"previous": "pending", "decision": "approved"})
        cls.system = AuditEntry.objects.create(action="security.mfa_reset", target_type="accounts.user",
            target_id=str(cls.other.pk), reason="Identidade conferida por procedimento técnico.")

    def setUp(self):
        self.client.force_login(self.actor)

    def url(self, obj=None):
        if obj is None:
            return reverse("management_api:audit_entries")
        return reverse("management_api:audit_entry", kwargs={"entry_id": str(obj.pk)})

    def grant(self):
        self.actor.user_permissions.add(self.permission)

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn("Location", response)

    def test_authentication_is_required_for_list_and_detail(self):
        for obj in (None, self.reviewed):
            self.assert_error(Client().get(self.url(obj)), 401, "authentication_required")
        self.grant()
        User.objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assert_error(self.client.get(self.url()), 401, "authentication_required")

    def test_staff_or_registration_permissions_do_not_grant_audit_access(self):
        for obj in (None, self.reviewed):
            self.assert_error(self.client.get(self.url(obj)), 403, "permission_denied")
        self.actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="registrations"))
        self.assert_error(self.client.get(self.url()), 403, "permission_denied")

    def test_audit_permission_without_staff_does_not_grant_access(self):
        self.grant()
        User.objects.filter(pk=self.actor.pk).update(is_staff=False)
        for obj in (None, self.reviewed):
            self.assert_error(self.client.get(self.url(obj)), 403, "permission_denied")

    def test_group_permission_works_and_revocation_is_effective_on_next_request(self):
        group = Group.objects.create(name="Equipe técnica de auditoria")
        group.permissions.add(self.permission)
        self.actor.groups.add(group)
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        group.permissions.clear()
        self.assert_error(self.client.get(self.url(self.reviewed)), 403, "permission_denied")

    def test_session_capability_is_independent_from_registration_permissions(self):
        self.grant()
        data = self.client.get(reverse("management_api:session")).json()["data"]["permissions"]
        self.assertTrue(data["view_audit"])
        self.assertFalse(data["view_registrations"])
        self.assertFalse(data["review_category"])
        self.assertFalse(data["export_registrations"])

    def test_list_has_minimal_actor_and_target_without_reason_or_details(self):
        self.grant()
        response = self.client.get(self.url())
        rows = response.json()["data"]["entries"]
        self.assertEqual(len(rows), 3)
        row = next(item for item in rows if item["id"] == str(self.reviewed.pk))
        self.assertEqual(set(row), {"id", "action", "actor", "target", "created_at"})
        self.assertEqual(row["actor"], {"id": str(self.actor.pk), "full_name": self.actor.full_name})
        self.assertEqual(row["target"], {"type": "registrations.registration", "id": str(self.registration.pk)})
        self.assertNotContains(response, self.actor.email)
        self.assertNotContains(response, self.owner.email)
        self.assertNotContains(response, self.reviewed.reason)
        self.assertNotContains(response, "password")
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn("Access-Control-Allow-Origin", response)

    def test_detail_returns_reason_and_structured_review_changes(self):
        self.grant()
        data = self.client.get(self.url(self.reviewed)).json()["data"]["entry"]
        self.assertEqual(data["reason"], self.reviewed.reason)
        self.assertEqual(data["details"], {"previous": "pending", "decision": "approved"})
        self.assertEqual(data["created_at"], self.reviewed.created_at.isoformat())

    def test_created_registration_keeps_original_category_and_amount(self):
        self.grant()
        category = self.price.category
        category.name_pt = "Nome alterado depois da inscrição"
        category.save(update_fields=["name_pt"])
        data = self.client.get(self.url(self.created)).json()["data"]["entry"]
        self.assertEqual(data["details"], {"category_id": str(category.pk), "amount": "123.45", "currency": "BRL"})

    def test_system_and_deleted_actors_are_null_without_losing_entries(self):
        self.grant()
        self.assertIsNone(self.client.get(self.url(self.system)).json()["data"]["entry"]["actor"])
        ephemeral = factories.user("ephemeral@example.org", is_staff=True)
        obj = AuditEntry.objects.create(actor=ephemeral, action="security.mfa_disabled", target_type="accounts.user",
                                        target_id=str(ephemeral.pk))
        identifier = str(ephemeral.pk)
        ephemeral.delete()
        data = self.client.get(self.url(obj)).json()["data"]["entry"]
        self.assertIsNone(data["actor"])
        self.assertEqual(data["target"]["id"], identifier)

    def test_exact_filters_combine_instead_of_returning_unrelated_records(self):
        self.grant()
        values = {"action": self.reviewed.action, "actor_id": str(self.actor.pk),
                  "target_type": self.reviewed.target_type, "target_id": self.reviewed.target_id}
        for name, value in values.items():
            rows = self.client.get(self.url(), {name: value}).json()["data"]["entries"]
            self.assertTrue(any(item["id"] == str(self.reviewed.pk) for item in rows))
            if name in ("action", "actor_id"):
                self.assertEqual(len(rows), 1)
        rows = self.client.get(self.url(), values).json()["data"]["entries"]
        self.assertEqual([item["id"] for item in rows], [str(self.reviewed.pk)])
        values["actor_id"] = str(self.other.pk)
        self.assertEqual(self.client.get(self.url(), values).json()["data"]["entries"], [])

    def test_time_filters_require_timezone_and_use_inclusive_start_exclusive_end(self):
        self.grant()
        start = datetime(2026, 10, 1, 12, tzinfo=datetime_timezone.utc)
        AuditEntry.objects.filter(pk=self.created.pk).update(created_at=start - timedelta(seconds=1))
        AuditEntry.objects.filter(pk=self.reviewed.pk).update(created_at=start)
        AuditEntry.objects.filter(pk=self.system.pk).update(created_at=start + timedelta(hours=1))
        response = self.client.get(self.url(), {"created_from": "2026-10-01T09:00:00-03:00",
                                               "created_until": "2026-10-01T13:00:00Z"})
        self.assertEqual([row["id"] for row in response.json()["data"]["entries"]], [str(self.reviewed.pk)])
        for values in ({"created_from": "2026-10-01T12:00:00"}, {"created_until": "2026-10-01"},
                       {"created_from": "2026-13-01T12:00:00Z"}, {"created_from": "bad"},
                       {"created_from": start.isoformat(), "created_until": start.isoformat()},
                       {"created_from": (start + timedelta(hours=1)).isoformat(), "created_until": start.isoformat()}):
            self.assert_error(self.client.get(self.url(), values), 422, "validation_error")

    def test_pagination_is_bounded_and_deterministic_for_equal_timestamps(self):
        self.grant()
        AuditEntry.objects.all().update(created_at=timezone.now())
        expected = list(AuditEntry.objects.order_by("-pk").values_list("pk", flat=True))
        identifiers = []
        for page in range(1, 4):
            data = self.client.get(self.url(), {"page": page, "page_size": 1}).json()["data"]
            self.assertEqual(data["pagination"]["total"], 3)
            self.assertEqual(data["pagination"]["pages"], 3)
            identifiers.extend(row["id"] for row in data["entries"])
        self.assertEqual(identifiers, [str(value) for value in expected])
        self.assert_error(self.client.get(self.url(), {"page": 4, "page_size": 1}), 404, "page_not_found")

    def test_empty_results_return_page_one_with_zero_total(self):
        self.grant()
        data = self.client.get(self.url(), {"action": "unknown.action"}).json()["data"]
        self.assertEqual(data["entries"], [])
        self.assertEqual(data["pagination"], {"page": 1, "page_size": 50, "total": 0, "pages": 1,
                                              "has_next": False, "has_previous": False})

    def test_invalid_and_repeated_query_fields_are_rejected(self):
        self.grant()
        for values in ({"page": "0"}, {"page": "bad"}, {"page_size": "0"}, {"page_size": "101"},
                       {"actor_id": "not-a-uuid"}, {"action": "x" * 65}, {"target_type": "x" * 65},
                       {"target_id": "x" * 65}, {"created_from": "x" * 41}):
            self.assert_error(self.client.get(self.url(), values), 422, "validation_error")
        self.assert_error(self.client.get(self.url(), {"event_id": str(self.event.pk)}), 400, "unknown_fields")
        self.assert_error(self.client.get(self.url() + "?action=a&action=b"), 400, "invalid_payload")
        self.assert_error(self.client.get(self.url(self.reviewed), {"details": "true"}), 400, "unknown_fields")
        self.assert_error(self.client.get(self.url(self.reviewed) + "?a=1&a=2"), 400, "invalid_payload")

    def test_malformed_or_missing_detail_ids_have_the_same_json_404(self):
        self.grant()
        for identifier in ("invalid", str(uuid.uuid4())):
            url = reverse("management_api:audit_entry", kwargs={"entry_id": identifier})
            self.assert_error(self.client.get(url), 404, "audit_entry_not_found")

    def test_both_routes_are_read_only_and_do_not_change_history(self):
        self.grant()
        before = list(AuditEntry.objects.order_by("pk").values())
        for obj in (None, self.reviewed):
            for method in ("post", "put", "patch", "delete"):
                response = getattr(self.client, method)(self.url(obj), {"reason": "overwrite"}, content_type="application/json")
                self.assert_error(response, 405, "method_not_allowed")
                self.assertEqual(response["Allow"], "GET")
        self.client.get(self.url())
        self.client.get(self.url(self.reviewed))
        self.assertEqual(list(AuditEntry.objects.order_by("pk").values()), before)

    def test_unknown_actions_and_extra_details_do_not_expose_internal_payloads(self):
        self.grant()
        obj = AuditEntry.objects.create(actor=self.actor, action="future.internal_action", target_type="accounts.user",
            target_id=str(self.actor.pk), details={"password": "synthetic-private-value", "nested": {"token": "hidden"}})
        self.assertEqual(self.client.get(self.url(obj)).json()["data"]["entry"]["details"], {})
        AuditEntry.objects.filter(pk=self.reviewed.pk).update(details={"previous": "pending", "decision": "approved",
            "token": "synthetic-private-value", "secret_ciphertext": "hidden", "profile": {"email": self.owner.email}})
        response = self.client.get(self.url(self.reviewed))
        self.assertEqual(response.json()["data"]["entry"]["details"], {"previous": "pending", "decision": "approved"})
        self.assertNotContains(response, "synthetic-private-value")
        self.assertNotContains(response, self.owner.email)

    def test_invalid_detail_types_and_values_are_omitted_without_coercion(self):
        self.grant()
        for details in (["unexpected"], {"previous": {"secret": "hidden"}, "decision": "hidden"}):
            AuditEntry.objects.filter(pk=self.reviewed.pk).update(details=details)
            self.assertEqual(self.client.get(self.url(self.reviewed)).json()["data"]["entry"]["details"], {})
        AuditEntry.objects.filter(pk=self.created.pk).update(details={"category_id": "invalid", "amount": "NaN", "currency": {"secret": "hidden"}})
        self.assertEqual(self.client.get(self.url(self.created)).json()["data"]["entry"]["details"], {})
        obj = AuditEntry.objects.create(action="registration.exported", target_type="registrations.event", target_id=str(self.event.pk),
            details={"rows": True, "search_applied": "false", "status": "hidden", "review": []})
        self.assertEqual(self.client.get(self.url(obj)).json()["data"]["entry"]["details"], {})

    def test_export_and_mfa_details_expose_only_operational_summaries(self):
        self.grant()
        obj = AuditEntry.objects.create(action="registration.exported", target_type="registrations.event", target_id=str(self.event.pk),
            details={"rows": 0, "status": "", "review": "pending", "search_applied": False, "q": self.owner.email})
        self.assertEqual(self.client.get(self.url(obj)).json()["data"]["entry"]["details"],
                         {"rows": 0, "status": "", "review": "pending", "search_applied": False})
        obj = AuditEntry.objects.create(actor=self.actor, action="security.mfa_verified", target_type="accounts.user", target_id=str(self.actor.pk),
            details={"method": "recovery", "code": "synthetic-private-value"})
        self.assertEqual(self.client.get(self.url(obj)).json()["data"]["entry"]["details"], {"method": "recovery"})
        obj.target_type = "future.private_object"
        obj.save(update_fields=["target_type"])
        self.assertEqual(self.client.get(self.url(obj)).json()["data"]["entry"]["details"], {})

    @override_settings(STAFF_MFA_REQUIRED=True)
    def test_mandatory_mfa_blocks_both_routes_before_enrollment(self):
        self.grant()
        for obj in (None, self.reviewed):
            self.assert_error(self.client.get(self.url(obj)), 403, "mfa_enrollment_required")

    def test_enrolled_mfa_requires_verification_and_revocation_blocks_access(self):
        self.grant()
        factor = StaffMFA.objects.create(user=self.actor, enabled=True,
                                        secret_ciphertext=mfa.cipher().encrypt(TEST_SECRET.encode()).decode())
        for obj in (None, self.reviewed):
            self.assert_error(self.client.get(self.url(obj)), 403, "mfa_required")
        now = timezone.now()
        with patch("accounts.mfa.timezone.now", return_value=now):
            response = self.client.post(reverse("staff_mfa:verify"), {"code": pyotp.TOTP(TEST_SECRET).at(now)}, content_type="application/json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.client.get(self.url()).status_code, 200)
            self.assertEqual(self.client.get(self.url(self.reviewed)).status_code, 200)
        factor.version = uuid.uuid4()
        factor.save(update_fields=["version"])
        self.assert_error(self.client.get(self.url()), 403, "mfa_required")

    def test_database_failure_returns_json_503_without_internal_details(self):
        self.grant()
        for obj in (None, self.reviewed):
            with patch("operations.audit_api.AuditEntry.objects.select_related", side_effect=DatabaseError("synthetic-private-value")):
                response = self.client.get(self.url(obj))
            self.assert_error(response, 503, "operation_unavailable")
            self.assertNotContains(response, "synthetic-private-value", status_code=503)

    @override_settings(REGISTRATION_EVENT_CODE="not-configured")
    def test_global_audit_history_does_not_depend_on_current_event_configuration(self):
        self.grant()
        self.assertEqual(self.client.get(self.url()).json()["data"]["pagination"]["total"], 3)
        self.assertEqual(self.client.get(self.url(self.system)).status_code, 200)

    def test_real_review_and_cancel_actions_are_traceable_without_editing_history(self):
        self.grant()
        self.actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="registrations", codename__in=[
            "view_registration", "review_registration", "cancel_registration"]))
        review_category(actor=self.actor, registration_id=self.registration.pk, decision="rejected", reason="Critério não atendido.")
        cancel_registration(actor=self.actor, registration_id=self.registration.pk, reason="Solicitação do participante.")
        rows = self.client.get(self.url(), {"target_type": "registrations.registration", "target_id": str(self.registration.pk)}).json()["data"]["entries"]
        self.assertEqual(rows[0]["action"], "registration.cancelled")
        obj = AuditEntry.objects.get(action="registration.category_reviewed", details__decision="rejected")
        data = self.client.get(self.url(obj)).json()["data"]["entry"]
        self.assertEqual(data["actor"]["id"], str(self.actor.pk))
        self.assertEqual(data["reason"], "Critério não atendido.")
        self.assertEqual(data["details"]["decision"], "rejected")
