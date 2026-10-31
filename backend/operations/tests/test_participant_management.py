import io
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.db import DatabaseError, connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
import pyotp

from accounts import mfa
from accounts.models import AccountActionToken, ParticipantProfile, StaffMFA, User
from accounts.services import request_account_email, update_profile
from accounts.tests.test_mfa import TEST_SECRET
from certificates.models import CertificateDraft, CertificateEligibility
from operations.models import AuditEntry
from operations.participants import ACTION, correct_participant
from registrations.services import register
from registrations.tests import factories


@override_settings(STAFF_MFA_REQUIRED=False, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ParticipantManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = factories.user("operator@example.invalid", is_staff=True)
        cls.owner = factories.user("person@example.invalid")
        cls.profile = ParticipantProfile.objects.create(user=cls.owner, institution="Instituição original", country="BR")
        cls.unregistered = User.objects.create_user("unconfirmed@example.invalid", full_name="Sem inscrição", is_active=False)
        cls.legacy = factories.user("legacy@example.invalid")
        cls.superuser = factories.user("technical@example.invalid", is_superuser=True, is_staff=False)
        cls.event = factories.event()
        cls.price = factories.price(cls.event)
        cls.registration, _ = register(user=cls.owner, event_id=cls.event.pk, price_id=cls.price.pk)
        cls.foreign_event = factories.event("foreign")
        cls.foreign_price = factories.price(cls.foreign_event)
        cls.foreign, _ = register(user=cls.legacy, event_id=cls.foreign_event.pk, price_id=cls.foreign_price.pk)

    def setUp(self):
        self.client.force_login(self.actor)

    def url(self, name="participant", target=None):
        kwargs = None if name == "participants" else {"participant_id": str((target or self.owner).pk)}
        return reverse("management_api:" + name, kwargs=kwargs)

    def grant(self, *codes):
        self.actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="accounts", codename__in=codes))

    def detail(self, target=None):
        return self.client.get(self.url(target=target)).json()["data"]["participant"]

    def payload(self, target=None, **values):
        data = self.detail(target)
        return {"full_name": data["full_name"], "institution": data["institution"], "country": data["country"],
                "preferred_language": data["preferred_language"], "revision": data["revision"],
                "reason": "Dados conferidos com o participante.", **values}

    def post(self, values, target=None, **extra):
        return self.client.post(self.url(target=target), values, content_type="application/json", **extra)

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn("Location", response)

    def test_anonymous_inactive_and_nonstaff_are_denied(self):
        for name in ("participants", "participant", "participant_history"):
            self.assert_error(Client().get(self.url(name)), 401, "authentication_required")
        self.grant("view_participant", "correct_participant")
        User.objects.filter(pk=self.actor.pk).update(is_staff=False)
        self.assert_error(self.client.get(self.url()), 403, "permission_denied")
        User.objects.filter(pk=self.actor.pk).update(is_staff=True, is_active=False)
        self.assert_error(self.client.get(self.url("participants")), 401, "authentication_required")

    def test_staff_and_registration_permissions_do_not_grant_participant_access(self):
        self.actor.user_permissions.add(*Permission.objects.filter(content_type__app_label="registrations"))
        for name in ("participants", "participant", "participant_history"):
            self.assert_error(self.client.get(self.url(name)), 403, "permission_denied")

    def test_correction_permission_alone_is_insufficient(self):
        self.grant("correct_participant")
        self.assert_error(self.client.get(self.url()), 403, "permission_denied")
        self.assert_error(self.post({}), 403, "permission_denied")

    def test_view_only_cannot_correct_or_enter_technical_admin(self):
        self.grant("view_participant")
        values = self.payload(full_name="Nome corrigido")
        self.assert_error(self.post(values), 403, "permission_denied")
        self.assertEqual(self.client.get(reverse("admin:accounts_user_change", args=[self.owner.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:auth_group_changelist")).status_code, 403)
        self.assertEqual(self.client.get(reverse("management_api:registrations")).status_code, 403)
        self.assertEqual(self.client.get(reverse("management_api:audit_entries")).status_code, 403)

    def test_capabilities_are_separate_and_revocation_is_effective(self):
        self.grant("view_participant", "correct_participant")
        flags = self.client.get(reverse("management_api:session")).json()["data"]["permissions"]
        self.assertTrue(flags["view_participants"])
        self.assertTrue(flags["correct_participants"])
        self.assertFalse(flags["view_registrations"])
        self.assertFalse(flags["view_audit"])
        self.actor.user_permissions.clear()
        self.assert_error(self.client.get(self.url()), 403, "permission_denied")

    def test_list_includes_unregistered_and_legacy_accounts_but_excludes_privileged_accounts(self):
        self.grant("view_participant")
        response = self.client.get(self.url("participants"))
        rows = response.json()["data"]["participants"]
        self.assertEqual({row["id"] for row in rows}, {str(self.owner.pk), str(self.unregistered.pk), str(self.legacy.pk)})
        owner = next(row for row in rows if row["id"] == str(self.owner.pk))
        self.assertEqual(set(owner), {"id", "full_name", "email", "is_active", "email_verified", "joined_at", "has_registration"})
        self.assertTrue(owner["has_registration"])
        legacy = next(row for row in rows if row["id"] == str(self.legacy.pk))
        self.assertFalse(legacy["has_registration"])
        self.assertNotIn(str(self.foreign.pk), response.content.decode())
        for forbidden in ("password", "is_staff", "is_superuser", "session_version", "csrf", "groups"):
            self.assertNotIn(forbidden, response.content.decode())

    def test_filters_search_pagination_and_unknown_queries(self):
        self.grant("view_participant")
        cases = [({"q": self.owner.email.upper()}, self.owner), ({"q": str(self.owner.pk)}, self.owner),
                 ({"email_status": "unverified", "account_status": "inactive", "registration": "not_registered"}, self.unregistered),
                 ({"registration": "registered"}, self.owner)]
        for query, expected in cases:
            with self.subTest(query=query):
                data = self.client.get(self.url("participants"), query).json()["data"]
                self.assertEqual([row["id"] for row in data["participants"]], [str(expected.pk)])
        data = self.client.get(self.url("participants"), {"page_size": 1}).json()["data"]
        self.assertEqual(data["pagination"]["total"], 3)
        self.assertEqual(len(data["participants"]), 1)
        self.assert_error(self.client.get(self.url("participants"), {"page": 999}), 404, "page_not_found")
        for query in ({"page_size": 101}, {"account_status": "yes"}, {"email_status": "yes"}, {"registration": "paid"}):
            self.assert_error(self.client.get(self.url("participants"), query), 422, "validation_error")
        self.assert_error(self.client.get(self.url("participants"), {"event_id": str(self.foreign_event.pk)}), 400, "unknown_fields")
        self.assert_error(self.client.get(self.url("participants") + "?q=a&q=b"), 400, "invalid_payload")

    def test_detail_has_safe_fields_and_only_current_event_registration_summary(self):
        self.grant("view_participant")
        data = self.detail()
        self.assertEqual(data["registration"], {"id": str(self.registration.pk), "status": "pending"})
        self.assertFalse(data["actions"]["can_correct"])
        self.assertFalse(data["actions"]["can_view_registration"])
        self.assertEqual(data["institution"], self.profile.institution)
        self.assertRegex(data["revision"], r"^[0-9a-f]{64}$")
        self.assertIsNone(self.detail(self.legacy)["registration"])
        self.assertEqual(self.detail(self.legacy)["country"], "")
        for key in ("password", "amount", "email_verified_at", "session_version", "is_staff", "is_superuser"):
            self.assertNotIn(key, data)

    def test_hidden_malformed_and_missing_targets_have_same_not_found_response(self):
        self.grant("view_participant", "correct_participant")
        payload = self.payload(full_name="Nome corrigido")
        targets = [str(self.actor.pk), str(self.superuser.pk), str(uuid.uuid4()), "invalid"]
        for target in targets:
            for name in ("participant", "participant_history"):
                url = reverse("management_api:"+name, kwargs={"participant_id": target})
                self.assert_error(self.client.get(url), 404, "participant_not_found")
                if name == "participant":
                    self.assert_error(self.client.post(url, payload, content_type="application/json"), 404, "participant_not_found")

    def test_valid_correction_is_atomic_audited_and_preserves_security_fields(self):
        self.grant("view_participant", "correct_participant")
        participant_session = Client()
        participant_session.force_login(self.owner)
        old_hash = self.owner.password
        request_account_email(self.owner.email, "reset")
        token = AccountActionToken.objects.get(user=self.owner)
        values = self.payload(full_name="Nome corrigido", institution="Nova instituição", country="US", preferred_language="en")
        response = self.post(values)
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        self.assertTrue(data["changed"])
        self.assertNotEqual(data["participant"]["revision"], values["revision"])
        self.owner.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.owner.full_name, "Nome corrigido")
        self.assertEqual(self.owner.password, old_hash)
        self.assertTrue(self.owner.is_active)
        self.assertIsNotNone(self.owner.email_verified_at)
        self.assertEqual((self.profile.institution, str(self.profile.country), self.profile.preferred_language), ("Nova instituição", "US", "en"))
        token.refresh_from_db()
        self.assertIsNone(token.revoked_at)
        self.assertEqual(participant_session.get(reverse("auth_api:session")).json()["data"]["authenticated"], True)
        entry = AuditEntry.objects.get(action=ACTION)
        self.assertEqual(entry.actor, self.actor)
        self.assertEqual(entry.target_id, str(self.owner.pk))
        self.assertEqual(entry.reason, values["reason"])
        self.assertEqual(entry.details, {"fields": ["country", "full_name", "institution", "preferred_language"]})
        self.assertNotIn("Nome corrigido", str(entry.details))

    def test_missing_profile_can_be_corrected_without_activating_or_verifying_account(self):
        self.grant("view_participant", "correct_participant")
        values = self.payload(self.unregistered, institution="Instituição conferida", country="BR")
        self.assertEqual(self.post(values, self.unregistered).status_code, 200)
        self.unregistered.refresh_from_db()
        self.assertFalse(self.unregistered.is_active)
        self.assertIsNone(self.unregistered.email_verified_at)
        self.assertEqual(self.unregistered.profile.institution, "Instituição conferida")

    def test_retries_and_noop_do_not_duplicate_audit(self):
        self.grant("view_participant", "correct_participant")
        original = self.payload()
        self.assertFalse(self.post(original).json()["data"]["changed"])
        values = {**original, "full_name": "Nome corrigido"}
        self.assertTrue(self.post(values).json()["data"]["changed"])
        self.assertFalse(self.post(values).json()["data"]["changed"])
        self.assertEqual(AuditEntry.objects.filter(action=ACTION).count(), 1)

    def test_stale_revision_cannot_overwrite_a_previous_correction_or_self_edit(self):
        self.grant("view_participant", "correct_participant")
        original = self.payload()
        self.assertEqual(self.post({**original, "institution": "Primeira correção"}).status_code, 200)
        self.assert_error(self.post({**original, "country": "US"}), 409, "participant_changed")
        current = self.payload()
        update_profile(self.owner, {"full_name": "Alterado pelo participante", "institution": "Própria", "country": "BR", "preferred_language": "pt-br"})
        self.assert_error(self.post({**current, "country": "US"}), 409, "participant_changed")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.institution, "Própria")

    def test_validations_and_unknown_fields_cannot_change_protected_state(self):
        self.grant("view_participant", "correct_participant")
        original = self.payload()
        for field, value in (("country", "ZZ"), ("preferred_language", "fr"), ("full_name", " "),
                             ("reason", " "), ("reason", "a"*1001), ("revision", "invalid"), ("institution", "a"*256)):
            with self.subTest(field=field):
                self.assert_error(self.post({**original, field: value}), 422, "validation_error")
        for field in ("email", "is_active", "is_staff", "is_superuser", "email_verified_at", "password", "user_permissions", "registration_status", "actor"):
            self.assert_error(self.post({**original, field: "true"}), 400, "unknown_fields")
        for field in ("full_name", "preferred_language", "revision", "reason"):
            values = {key: value for key, value in original.items() if key != field}
            self.assert_error(self.post(values), 422, "validation_error")
        self.assertFalse(AuditEntry.objects.filter(action=ACTION).exists())

    def test_csrf_method_and_payload_contracts(self):
        self.grant("view_participant", "correct_participant")
        values = self.payload()
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        self.assert_error(client.post(self.url(), values, content_type="application/json"), 403, "csrf_failed")
        self.assert_error(self.client.put(self.url(), values, content_type="application/json"), 405, "method_not_allowed")
        self.assert_error(self.client.post(self.url(), '{"reason":"one","reason":"two"}', content_type="application/json"), 400, "invalid_payload")
        self.assert_error(self.post({**values, "country": ["BR"]}), 400, "invalid_payload")
        self.assert_error(self.post({**values, "reason": "a"*33000}), 413, "payload_too_large")
        self.assert_error(self.client.post(self.url(), values), 415, "unsupported_media_type")
        self.assertEqual(self.client.post(self.url(), urlencode(values), content_type="application/x-www-form-urlencoded").status_code, 200)

    def test_audit_failure_rolls_back_name_and_profile(self):
        self.grant("view_participant", "correct_participant")
        values = self.payload(full_name="Nome corrigido", institution="Instituição nova")
        with patch("operations.participants.audit", side_effect=DatabaseError("synthetic failure")):
            self.assert_error(self.post(values), 503, "operation_unavailable")
        self.owner.refresh_from_db()
        self.profile.refresh_from_db()
        self.assertEqual(self.owner.full_name, "Participante de teste")
        self.assertEqual(self.profile.institution, "Instituição original")
        self.assertFalse(AuditEntry.objects.filter(action=ACTION).exists())

    def test_fresh_permission_and_target_privileges_are_rechecked_before_writing(self):
        self.grant("view_participant", "correct_participant")
        values = self.payload(full_name="Nome corrigido")
        def revoke(**kwargs):
            self.actor.user_permissions.clear()
            return correct_participant(**kwargs)
        with patch("operations.participant_api.correct_participant", side_effect=revoke):
            self.assert_error(self.post(values), 403, "permission_denied")
        self.grant("view_participant", "correct_participant")
        def promote(**kwargs):
            User.objects.filter(pk=self.owner.pk).update(is_staff=True)
            return correct_participant(**kwargs)
        with patch("operations.participant_api.correct_participant", side_effect=promote):
            self.assert_error(self.post(values), 404, "participant_not_found")
        self.assertFalse(AuditEntry.objects.filter(action=ACTION).exists())

    def test_history_contains_only_corrections_of_this_participant_and_safe_fields(self):
        self.grant("view_participant", "correct_participant")
        self.post(self.payload(full_name="Nome corrigido"))
        AuditEntry.objects.create(action="security.mfa_disabled", target_type="accounts.user", target_id=str(self.owner.pk), details={"private": "synthetic-private"})
        AuditEntry.objects.create(action=ACTION, target_type="accounts.user", target_id=str(self.legacy.pk), reason="Outro cadastro", details={"fields": ["full_name"]})
        entry = AuditEntry.objects.get(action=ACTION, target_id=str(self.owner.pk))
        entry.details["private"] = "synthetic-private"
        entry.save(update_fields=["details"])
        response = self.client.get(self.url("participant_history"))
        rows = response.json()["data"]["history"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["actor"], {"id": str(self.actor.pk), "full_name": self.actor.full_name})
        self.assertEqual(rows[0]["details"], {"fields": ["full_name"]})
        self.assertEqual(rows[0]["reason"], "Dados conferidos com o participante.")
        self.assertNotIn("synthetic-private", response.content.decode())
        self.assertNotIn(self.actor.email, response.content.decode())
        for fields in (["full_name", "password"], "full_name", {"full_name": "private"}, [True], [["full_name"]]):
            entry.details = {"fields": fields}
            entry.save(update_fields=["details"])
            self.assertEqual(self.client.get(self.url("participant_history")).json()["data"]["history"][0]["details"], {})
        self.assert_error(self.client.get(self.url("participant_history"), {"action": "security.mfa_disabled"}), 400, "unknown_fields")
        self.assert_error(self.client.get(self.url("participant_history"), {"page": 99}), 404, "page_not_found")

    def test_correction_does_not_rewrite_registration_or_certificate_snapshots(self):
        self.grant("view_participant", "correct_participant")
        draft = CertificateDraft.objects.create(
            eligibility=CertificateEligibility.objects.create(registration=self.registration),
            participant_name=self.owner.full_name, event_title=self.event.title,
            event_starts_on=self.event.starts_on, event_ends_on=self.event.ends_on,
            preparation_reason="Rascunho sintético", prepared_by=self.actor,
        )
        old = (self.registration.amount, self.registration.currency, self.registration.category_name, self.registration.status)
        self.assertEqual(self.post(self.payload(full_name="Nome corrigido")).status_code, 200)
        self.registration.refresh_from_db()
        draft.refresh_from_db()
        self.assertEqual((self.registration.amount, self.registration.currency, self.registration.category_name, self.registration.status), old)
        self.assertEqual(draft.participant_name, "Participante de teste")

    @override_settings(STAFF_MFA_REQUIRED=True)
    def test_mfa_enrollment_is_required_for_all_routes(self):
        self.grant("view_participant", "correct_participant")
        for name in ("participants", "participant", "participant_history"):
            self.assert_error(self.client.get(self.url(name)), 403, "mfa_enrollment_required")
        self.assert_error(self.post({}), 403, "mfa_enrollment_required")

    def test_active_factor_requires_valid_proof_even_when_global_requirement_is_false(self):
        self.grant("view_participant", "correct_participant")
        values = self.payload(full_name="Nome corrigido")
        StaffMFA.objects.create(user=self.actor, enabled=True, secret_ciphertext=mfa.cipher().encrypt(TEST_SECRET.encode()).decode())
        self.assert_error(self.client.get(self.url()), 403, "mfa_required")
        verification = self.client.post(reverse("staff_mfa:verify"), {"code": pyotp.TOTP(TEST_SECRET).now()}, content_type="application/json")
        self.assertEqual(verification.status_code, 200)
        self.assertEqual(self.post(values).status_code, 200)
        with patch("accounts.mfa.timezone.now", return_value=timezone.now()+timedelta(hours=2)):
            self.assert_error(self.client.get(self.url()), 403, "mfa_required")

    def test_service_cannot_bypass_mfa_through_an_unprotected_future_route(self):
        self.grant("view_participant", "correct_participant")
        values = self.payload(full_name="Nome corrigido")
        StaffMFA.objects.create(user=self.actor, enabled=True, secret_ciphertext=mfa.cipher().encrypt(TEST_SECRET.encode()).decode())
        middleware = [item for item in settings.MIDDLEWARE if item != "accounts.mfa_middleware.StaffMFAMiddleware"]
        with override_settings(MIDDLEWARE=middleware):
            self.assert_error(self.post(values), 403, "mfa_required")

    def test_roles_command_is_additive_and_does_not_create_or_assign_users(self):
        before = User.objects.count()
        group = Group.objects.create(name="CHAGS — Consulta de participantes")
        extra = Permission.objects.get(content_type__app_label="registrations", codename="view_registration")
        group.permissions.add(extra)
        for _ in range(2):
            call_command("setup_participant_roles", stdout=io.StringIO())
        self.assertEqual(User.objects.count(), before)
        self.assertFalse(group.user_set.exists())
        self.assertTrue(group.permissions.filter(pk=extra.pk).exists())
        self.assertTrue(group.permissions.filter(codename="view_participant").exists())
        self.assertFalse(group.permissions.filter(codename="correct_participant").exists())
        group = Group.objects.get(name="CHAGS — Correção de participantes")
        self.assertEqual(set(group.permissions.values_list("codename", flat=True)), {"view_participant", "correct_participant"})

    @override_settings(REGISTRATION_EVENT_CODE="not-configured")
    def test_missing_event_is_reported_without_editing_accounts(self):
        self.grant("view_participant", "correct_participant")
        self.assert_error(self.client.get(self.url("participants")), 404, "event_not_configured")
        self.assert_error(self.client.get(self.url()), 404, "event_not_configured")


@override_settings(STAFF_MFA_REQUIRED=False, PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class ParticipantCorrectionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = factories.user("participant@example.invalid")
        self.profile = ParticipantProfile.objects.create(user=self.owner, institution="Original", country="BR")
        factories.event()
        self.clients = []
        permissions = list(Permission.objects.filter(content_type__app_label="accounts", codename__in=("view_participant", "correct_participant")))
        # Distinct operators prevent the MFA middleware's operator lock from
        # serializing these requests before they contend for the participant.
        for index in range(2):
            actor = factories.user(f"operator{index}@example.invalid", is_staff=True)
            actor.user_permissions.add(*permissions)
            client = Client()
            client.force_login(actor)
            self.clients.append(client)
        self.url = reverse("management_api:participant", kwargs={"participant_id": str(self.owner.pk)})
        data = self.clients[0].get(self.url).json()["data"]["participant"]
        self.values = {name: data[name] for name in ("full_name", "institution", "country", "preferred_language", "revision")}
        self.values["reason"] = "Correção concorrente sintética."

    def concurrent(self, values):
        barrier = Barrier(2)

        def run(index):
            try:
                barrier.wait(timeout=10)
                response = self.clients[index].post(self.url, values[index], content_type="application/json")
                return response.status_code, response.json()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(run, range(2)))

    def test_different_profile_corrections_cannot_overwrite_each_other(self):
        values = [{**self.values, "institution": name} for name in ("Primeira", "Segunda")]
        results = self.concurrent(values)
        self.assertCountEqual([status for status, _ in results], [200, 409])
        winner = next(index for index, (status, _) in enumerate(results) if status == 200)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.institution, values[winner]["institution"])
        error = next(body for status, body in results if status == 409)
        self.assertEqual(error["error"]["code"], "participant_changed")
        self.assertEqual(AuditEntry.objects.filter(action=ACTION).count(), 1)

    def test_identical_concurrent_corrections_create_one_history_entry(self):
        values = {**self.values, "institution": "Instituição corrigida"}
        results = self.concurrent([values, values])
        self.assertEqual([status for status, _ in results], [200, 200])
        self.assertCountEqual([body["data"]["changed"] for _, body in results], [True, False])
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.institution, values["institution"])
        self.assertEqual(AuditEntry.objects.filter(action=ACTION).count(), 1)
