import io
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
import pyotp

from accounts import mfa
from accounts.models import StaffMFA, User
from accounts.tests.test_mfa import TEST_SECRET
from certificates.models import CertificateDraft, CertificateEligibility
from certificates.services import prepare_draft
from operations.models import AuditEntry
from registrations.models import Registration
from registrations.services import cancel_registration, register
from registrations.tests import factories


@override_settings(STAFF_MFA_REQUIRED=False)
class CertificateApiTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = factories.user("certificates@example.org", is_staff=True)
        cls.owner = factories.user()
        cls.other = factories.user("other@example.org")
        cls.event = factories.event()
        cls.price = factories.price(cls.event, requires_review=True)
        cls.registration, _ = register(user=cls.owner, event_id=cls.event.pk, price_id=cls.price.pk)
        cls.other_registration, _ = register(user=cls.other, event_id=cls.event.pk, price_id=cls.price.pk)
        cls.foreign_event = factories.event("other-event")
        cls.foreign_price = factories.price(cls.foreign_event)
        cls.foreign, _ = register(user=cls.owner, event_id=cls.foreign_event.pk, price_id=cls.foreign_price.pk)

    def setUp(self):
        self.client.force_login(self.actor)
        self.participant = Client()
        self.participant.force_login(self.owner)

    def url(self, name="drafts", obj=None):
        return reverse("certificate_management:" + name, kwargs={"draft_id": str(obj.pk)} if obj else None)

    def own_url(self):
        return reverse("participant_api:certificates")

    def grant(self, *codes):
        codes = codes or ("view_registration", "view_certificatedraft", "prepare_certificatedraft")
        self.actor.user_permissions.set(Permission.objects.filter(content_type__app_label__in=["registrations", "certificates"], codename__in=codes))

    def prepare(self, *, registration=None, data=None, client=None):
        payload = data if data is not None else {"registration_id": str((registration or self.registration).pk),
                                                "reason": "Preparação para conferência interna."}
        return (client or self.client).post(self.url("prepare"), payload, content_type="application/json")

    def make_draft(self, registration=None):
        self.grant()
        response = self.prepare(registration=registration)
        self.assertEqual(response.status_code, 201)
        return CertificateDraft.objects.get(pk=response.json()["data"]["draft"]["id"])

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertIn("no-store", response["Cache-Control"])

    def test_management_requires_authentication_staff_and_distinct_permissions(self):
        for name in ("drafts", "prepare"):
            response = Client().get(self.url(name)) if name == "drafts" else self.prepare(client=Client())
            self.assert_error(response, 401, "authentication_required")
        for codes in ((), ("view_registration",), ("view_certificatedraft",), ("prepare_certificatedraft",)):
            self.actor.user_permissions.clear()
            if codes:
                self.grant(*codes)
            self.assert_error(self.client.get(self.url()), 403, "permission_denied")
            self.assert_error(self.prepare(), 403, "permission_denied")
        self.grant()
        User.objects.filter(pk=self.actor.pk).update(is_staff=False)
        self.assert_error(self.prepare(), 403, "permission_denied")
        User.objects.filter(pk=self.actor.pk).update(is_active=False)
        self.assert_error(self.client.get(self.url()), 401, "authentication_required")

    def test_viewer_can_consult_but_cannot_prepare_and_session_has_separate_capabilities(self):
        self.grant("view_registration", "view_certificatedraft")
        self.assertEqual(self.client.get(self.url()).status_code, 200)
        self.assert_error(self.prepare(), 403, "permission_denied")
        capabilities = self.client.get(reverse("management_api:session")).json()["data"]["permissions"]
        self.assertTrue(capabilities["view_certificate_drafts"])
        self.assertFalse(capabilities["prepare_certificate_drafts"])
        self.grant()
        self.assertTrue(self.client.get(reverse("management_api:session")).json()["data"]["permissions"]["prepare_certificate_drafts"])

    def test_preparation_creates_pending_eligibility_and_internal_snapshot_with_audit(self):
        self.grant()
        response = self.prepare()
        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]
        self.assertTrue(data["created"])
        draft = CertificateDraft.objects.get(pk=data["draft"]["id"])
        eligibility = draft.eligibility
        self.assertEqual(eligibility.decision, "pending")
        self.assertEqual(eligibility.criteria_version, "")
        self.assertIsNone(eligibility.reviewed_by_id)
        self.assertIsNone(eligibility.reviewed_at)
        self.assertEqual(draft.participant_name, self.owner.full_name)
        self.assertEqual(draft.event_title, self.event.title)
        self.assertEqual(draft.prepared_by_id, self.actor.pk)
        self.assertEqual(data["draft"]["state"], "draft")
        self.assertFalse(data["draft"]["issuance_enabled"])
        self.assertEqual(data["draft"]["issuance_block_reason"], "certificate_rules_not_configured")
        audit = AuditEntry.objects.get(action="certificate.draft_prepared")
        self.assertEqual(audit.target_id, str(draft.pk))
        self.assertEqual(audit.actor_id, self.actor.pk)
        self.assertEqual(audit.details, {"registration_id": str(self.registration.pk), "kind": "participation"})

    def test_preparation_audit_exposes_only_explicit_operational_fields(self):
        self.make_draft()
        audit = AuditEntry.objects.get(action="certificate.draft_prepared")
        details = {**audit.details, "internal_path": "synthetic-private-value", "participant_name": self.owner.full_name}
        AuditEntry.objects.filter(pk=audit.pk).update(details=details)
        self.actor.user_permissions.add(Permission.objects.get(content_type__app_label="operations", codename="view_auditentry"))
        response = self.client.get(reverse("management_api:audit_entry", kwargs={"entry_id": str(audit.pk)}))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["entry"]["details"],
                         {"registration_id": str(self.registration.pk), "kind": "participation"})
        self.assertNotContains(response, "synthetic-private-value")

    def test_registration_groups_do_not_implicitly_receive_certificate_permissions(self):
        call_command("setup_registration_roles", stdout=io.StringIO())
        self.assertFalse(Group.objects.filter(permissions__content_type__app_label="certificates").exists())
        self.actor.groups.add(Group.objects.get(name="CHAGS — Atendimento"))
        self.assert_error(self.prepare(), 403, "permission_denied")

    def test_repeated_preparation_keeps_original_snapshot_reason_and_audit(self):
        draft = self.make_draft()
        User.objects.filter(pk=self.owner.pk).update(full_name="Nome alterado depois")
        self.event.title = "Título alterado depois"
        self.event.starts_on += timedelta(days=1)
        self.event.save(update_fields=["title", "starts_on"])
        response = self.prepare(data={"registration_id": str(self.registration.pk), "reason": "Outro motivo"})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["data"]["created"])
        self.assertEqual(response.json()["data"]["draft"]["id"], str(draft.pk))
        draft.refresh_from_db()
        self.assertEqual(draft.participant_name, self.owner.full_name)
        self.assertEqual(draft.event_title, "Evento fictício")
        self.assertEqual(draft.preparation_reason, "Preparação para conferência interna.")
        self.assertEqual(CertificateDraft.objects.count(), 1)
        self.assertEqual(CertificateEligibility.objects.count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action="certificate.draft_prepared").count(), 1)

    def test_registration_confirmation_or_category_review_never_grants_eligibility(self):
        self.grant()
        Registration.objects.filter(pk=self.registration.pk).update(status="confirmed", category_review="approved")
        self.assertEqual(self.prepare().status_code, 201)
        self.assertEqual(CertificateEligibility.objects.get().decision, "pending")
        own = self.participant.get(self.own_url()).json()["data"]
        self.assertEqual(own["certificates"], [])
        self.assertEqual(own["eligibility_status"], "pending")
        self.assertFalse(own["issuance_enabled"])

    def test_cancelled_registration_blocks_preparation_without_creating_records(self):
        self.grant()
        self.actor.user_permissions.add(Permission.objects.get(content_type__app_label="registrations", codename="cancel_registration"))
        cancel_registration(actor=self.actor, registration_id=self.registration.pk, reason="Solicitado.")
        self.assert_error(self.prepare(), 409, "registration_cancelled")
        self.assertFalse(CertificateDraft.objects.exists())
        self.assertFalse(CertificateEligibility.objects.exists())
        self.assertEqual(self.participant.get(self.own_url()).json()["data"]["issuance_block_reason"], "registration_cancelled")

    def test_cancel_after_preparation_preserves_draft_without_issuing_or_reopening(self):
        draft = self.make_draft()
        self.actor.user_permissions.add(Permission.objects.get(content_type__app_label="registrations", codename="cancel_registration"))
        # Recheck permissions after a previous service may have cached them.
        actor = User.objects.get(pk=self.actor.pk)
        cancel_registration(actor=actor, registration_id=self.registration.pk, reason="Solicitado.")
        self.assert_error(self.prepare(), 409, "registration_cancelled")
        response = self.client.get(self.url("draft", draft))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["draft"]["registration_status"], "cancelled")
        self.assertFalse(response.json()["data"]["draft"]["issuance_enabled"])

    def test_preparation_does_not_change_existing_review_provenance(self):
        eligibility = CertificateEligibility.objects.create(registration=self.registration, decision="ineligible",
            criteria_version="synthetic-future-criteria", decision_reason="Avaliação fictícia anterior.",
            reviewed_by=self.actor, reviewed_at=timezone.now())
        before = {name: getattr(eligibility, name) for name in ("decision", "criteria_version", "decision_reason", "reviewed_by_id", "reviewed_at")}
        self.make_draft()
        eligibility.refresh_from_db()
        self.assertEqual({name: getattr(eligibility, name) for name in before}, before)

    def test_incomplete_snapshot_is_rejected_before_creating_eligibility(self):
        self.grant()
        User.objects.filter(pk=self.owner.pk).update(full_name="  ")
        self.assert_error(self.prepare(), 409, "certificate_data_incomplete")
        self.assertFalse(CertificateEligibility.objects.exists())

    def test_foreign_event_unknown_and_invalid_registration_ids_do_not_create_drafts(self):
        self.grant()
        for identifier in (str(self.foreign.pk), str(uuid.uuid4())):
            self.assert_error(self.prepare(data={"registration_id": identifier, "reason": "Teste"}), 404, "registration_not_found")
        self.assert_error(self.prepare(data={"registration_id": "invalid", "reason": "Teste"}), 422, "validation_error")
        self.assertFalse(CertificateDraft.objects.exists())

    def test_input_cannot_choose_eligibility_identity_workload_signature_or_issuance(self):
        self.grant()
        for field in ("eligibility_status", "participant_name", "event_title", "prepared_by_id", "kind", "workload_hours", "signature", "issuance_enabled", "file"):
            self.assert_error(self.prepare(data={"registration_id": str(self.registration.pk), "reason": "Teste", field: "forged"}), 400, "unknown_fields")
        for data in ({"registration_id": str(self.registration.pk)}, {"registration_id": str(self.registration.pk), "reason": "  "},
                     {"registration_id": str(self.registration.pk), "reason": "x" * 1001}):
            self.assert_error(self.prepare(data=data), 422, "validation_error")
        self.assertFalse(CertificateDraft.objects.exists())

    def test_shared_protocol_rejects_duplicate_nonstring_and_unsupported_payloads(self):
        self.grant()
        duplicate = '{"registration_id":"%s","reason":"a","reason":"b"}' % self.registration.pk
        self.assert_error(self.client.post(self.url("prepare"), duplicate, content_type="application/json"), 400, "invalid_payload")
        self.assert_error(self.prepare(data={"registration_id": str(self.registration.pk), "reason": True}), 400, "invalid_payload")
        self.assert_error(self.client.post(self.url("prepare"), "x", content_type="text/plain"), 415, "unsupported_media_type")
        payload = {"registration_id": str(self.registration.pk), "reason": "x" * 33000}
        self.assert_error(self.prepare(data=payload), 413, "payload_too_large")

    def test_list_and_detail_are_scoped_to_event_and_omit_account_credentials(self):
        draft = self.make_draft()
        foreign_draft, _ = prepare_draft(actor=User.objects.get(pk=self.actor.pk), event_id=self.foreign_event.pk,
                                       registration_id=self.foreign.pk, reason="Preparação fictícia de outro evento.")
        response = self.client.get(self.url())
        rows = response.json()["data"]["drafts"]
        self.assertEqual([row["id"] for row in rows], [str(draft.pk)])
        self.assertNotIn("preparation_reason", rows[0])
        self.assertNotContains(response, self.owner.email)
        self.assertNotContains(response, "password")
        self.assertNotIn("Access-Control-Allow-Origin", response)
        self.assert_error(self.client.get(self.url("draft", foreign_draft)), 404, "certificate_draft_not_found")
        self.assertEqual(self.client.get(self.url("draft", draft)).json()["data"]["draft"]["preparation_reason"], draft.preparation_reason)

    def test_list_filter_pagination_and_errors_are_strict(self):
        first = self.make_draft()
        second = self.make_draft(self.other_registration)
        first_page = self.client.get(self.url(), {"page_size": 1}).json()["data"]
        second_page = self.client.get(self.url(), {"page_size": 1, "page": 2}).json()["data"]
        self.assertEqual(first_page["pagination"]["total"], 2)
        self.assertNotEqual(first_page["drafts"][0]["id"], second_page["drafts"][0]["id"])
        rows = self.client.get(self.url(), {"registration_id": str(self.registration.pk)}).json()["data"]["drafts"]
        self.assertEqual([row["id"] for row in rows], [str(first.pk)])
        self.assert_error(self.client.get(self.url(), {"page": 3, "page_size": 1}), 404, "page_not_found")
        for values in ({"page": 0}, {"page_size": 101}, {"registration_id": "invalid"}):
            self.assert_error(self.client.get(self.url(), values), 422, "validation_error")
        self.assert_error(self.client.get(self.url(), {"event_id": str(self.foreign_event.pk)}), 400, "unknown_fields")
        self.assert_error(self.client.get(self.url() + "?page=1&page=2"), 400, "invalid_payload")
        self.assert_error(self.client.get(self.url("draft", second), {"expand": "all"}), 400, "unknown_fields")
        invalid = reverse("certificate_management:draft", kwargs={"draft_id": "invalid"})
        self.assert_error(self.client.get(invalid), 404, "certificate_draft_not_found")

    def test_participant_requires_verified_active_own_session_and_never_reads_drafts(self):
        draft = self.make_draft()
        self.assert_error(Client().get(self.own_url()), 401, "authentication_required")
        before = (CertificateDraft.objects.count(), CertificateEligibility.objects.count(), AuditEntry.objects.count())
        response = self.participant.get(self.own_url())
        data = response.json()["data"]
        self.assertEqual(data["registration_id"], str(self.registration.pk))
        self.assertEqual(data["certificates"], [])
        self.assertNotContains(response, str(draft.pk))
        self.assertNotContains(response, draft.preparation_reason)
        self.assertNotContains(response, str(self.actor.pk))
        self.assertEqual((CertificateDraft.objects.count(), CertificateEligibility.objects.count(), AuditEntry.objects.count()), before)
        self.assert_error(self.participant.get(self.own_url(), {"user_id": str(self.other.pk)}), 400, "unknown_fields")
        self.assert_error(self.participant.get(self.url()), 403, "permission_denied")
        User.objects.filter(pk=self.owner.pk).update(email_verified_at=None)
        self.assert_error(self.participant.get(self.own_url()), 403, "email_not_verified")
        User.objects.filter(pk=self.owner.pk).update(is_active=False)
        self.assert_error(self.participant.get(self.own_url()), 401, "authentication_required")

    def test_participant_without_registration_gets_no_records_and_no_data_is_created(self):
        user = factories.user("unregistered@example.org")
        self.participant.force_login(user)
        data = self.participant.get(self.own_url()).json()["data"]
        self.assertIsNone(data["registration_id"])
        self.assertEqual(data["issuance_block_reason"], "registration_required")
        self.assertEqual(data["certificates"], [])
        self.assertFalse(CertificateEligibility.objects.exists())

    @override_settings(REGISTRATION_EVENT_CODE="not-configured")
    def test_missing_event_is_explicit_in_staff_and_participant_api(self):
        self.grant()
        self.assert_error(self.client.get(self.url()), 404, "event_not_configured")
        self.assert_error(self.prepare(), 404, "event_not_configured")
        self.assert_error(self.participant.get(self.own_url()), 404, "event_not_configured")

    def test_database_failures_are_json_and_audit_failure_rolls_back_all_preparation(self):
        self.grant()
        with patch("certificates.services.audit", side_effect=DatabaseError("synthetic-private-value")):
            response = self.prepare()
        self.assert_error(response, 503, "operation_unavailable")
        self.assertNotContains(response, "synthetic-private-value", status_code=503)
        self.assertFalse(CertificateDraft.objects.exists())
        self.assertFalse(CertificateEligibility.objects.exists())
        with patch("certificates.api.current_event", side_effect=DatabaseError("synthetic-private-value")):
            self.assert_error(self.client.get(self.url()), 503, "operation_unavailable")
            self.assert_error(self.participant.get(self.own_url()), 503, "operation_unavailable")

    def test_csrf_is_required_and_cross_origin_preparation_is_denied(self):
        self.grant()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        self.assert_error(self.prepare(client=client), 403, "csrf_failed")
        token = client.get(reverse("management_api:session")).json()["data"]["csrf_token"]
        payload = {"registration_id": str(self.registration.pk), "reason": "Teste"}
        response = client.post(self.url("prepare"), payload, content_type="application/json", HTTP_X_CSRFTOKEN=token,
                               HTTP_ORIGIN="https://foreign.example")
        self.assert_error(response, 403, "csrf_failed")
        response = client.post(self.url("prepare"), payload, content_type="application/json", HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201)

    @override_settings(STAFF_MFA_REQUIRED=True)
    def test_management_requires_mfa_but_own_participant_status_does_not(self):
        self.grant()
        self.assert_error(self.client.get(self.url()), 403, "mfa_enrollment_required")
        self.assert_error(self.prepare(), 403, "mfa_enrollment_required")
        self.assertEqual(self.participant.get(self.own_url()).status_code, 200)

    def test_enabled_mfa_requires_challenge_and_then_allows_preparation(self):
        self.grant()
        StaffMFA.objects.create(user=self.actor, enabled=True, secret_ciphertext=mfa.cipher().encrypt(TEST_SECRET.encode()).decode())
        self.assert_error(self.prepare(), 403, "mfa_required")
        now = timezone.now()
        with patch("accounts.mfa.timezone.now", return_value=now):
            response = self.client.post(reverse("staff_mfa:verify"), {"code": pyotp.TOTP(TEST_SECRET).at(now)}, content_type="application/json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(self.prepare().status_code, 201)

    def test_no_files_or_issued_documents_are_created_and_draft_cannot_be_edited(self):
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=Path(directory)):
            draft = self.make_draft()
            self.assertEqual(list(Path(directory).rglob("*")), [])
            for method in ("post", "patch", "delete"):
                response = getattr(self.client, method)(self.url("draft", draft), {"state": "issued"}, content_type="application/json")
                self.assert_error(response, 405, "method_not_allowed")
            data = self.participant.get(self.own_url()).json()["data"]
            self.assertFalse(data["issuance_enabled"])
            self.assertEqual(data["certificates"], [])
            self.assertNotIn("file", data)
            self.assertNotIn("download_url", data)

    def test_service_rechecks_permissions_and_active_state_without_request_cache(self):
        self.grant()
        self.assertTrue(self.actor.has_perm("certificates.prepare_certificatedraft"))
        self.actor.user_permissions.clear()
        from django.core.exceptions import PermissionDenied
        with self.assertRaises(PermissionDenied):
            prepare_draft(actor=self.actor, event_id=self.event.pk, registration_id=self.registration.pk, reason="Teste")
        self.grant()
        User.objects.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            prepare_draft(actor=self.actor, event_id=self.event.pk, registration_id=self.registration.pk, reason="Teste")
