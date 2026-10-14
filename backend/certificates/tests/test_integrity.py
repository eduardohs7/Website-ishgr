from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connections, transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from accounts.models import User
from certificates.models import CertificateDraft, CertificateEligibility
from certificates.services import prepare_draft
from operations.models import AuditEntry
from registrations.services import cancel_registration, register
from registrations.tests import factories


def grant(actor, *, cancel=False):
    codes = ["view_registration", "view_certificatedraft", "prepare_certificatedraft"]
    if cancel:
        codes.append("cancel_registration")
    actor.user_permissions.add(*Permission.objects.filter(content_type__app_label__in=["registrations", "certificates"], codename__in=codes))


class CertificateIntegrityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.actor = factories.user("operator@example.org", is_staff=True)
        grant(cls.actor)
        cls.owner = factories.user()
        cls.event = factories.event()
        cls.price = factories.price(cls.event)
        cls.registration, _ = register(user=cls.owner, event_id=cls.event.pk, price_id=cls.price.pk)

    def test_registration_alone_does_not_create_certificate_records(self):
        self.assertFalse(CertificateEligibility.objects.exists())
        self.assertFalse(CertificateDraft.objects.exists())

    def test_database_rejects_invalid_or_incomplete_eligibility_decisions(self):
        eligibility = CertificateEligibility.objects.create(registration=self.registration)
        for values in ({"decision": "invalid"}, {"decision": "eligible"}, {"decision": "ineligible"},
                       {"criteria_version": "unapproved"}, {"reviewed_by": self.actor},
                       {"decision_reason": "Not a review"}, {"reviewed_at": timezone.now()}):
            with self.subTest(values=tuple(values)), self.assertRaises(IntegrityError), transaction.atomic():
                CertificateEligibility.objects.filter(pk=eligibility.pk).update(**values)
        eligibility.refresh_from_db()
        self.assertEqual(eligibility.decision, "pending")

    def test_database_requires_unique_registration_eligibility_and_one_draft(self):
        draft, _ = prepare_draft(actor=self.actor, event_id=self.event.pk, registration_id=self.registration.pk, reason="Teste")
        with self.assertRaises(IntegrityError), transaction.atomic():
            CertificateEligibility.objects.create(registration=self.registration)
        with self.assertRaises(IntegrityError), transaction.atomic():
            CertificateDraft.objects.create(eligibility=draft.eligibility, participant_name="Outra pessoa",
                event_title="Outro evento", event_starts_on=self.event.starts_on, event_ends_on=self.event.ends_on,
                preparation_reason="Teste", prepared_by=self.actor)

    def test_database_rejects_empty_snapshot_invalid_dates_and_unsupported_kind(self):
        draft, _ = prepare_draft(actor=self.actor, event_id=self.event.pk, registration_id=self.registration.pk, reason="Teste")
        for values in ({"participant_name": ""}, {"event_title": ""}, {"preparation_reason": ""},
                       {"event_ends_on": self.event.starts_on - timedelta(days=1)}, {"kind": "speaker"}):
            with self.subTest(values=tuple(values)), self.assertRaises(IntegrityError), transaction.atomic():
                CertificateDraft.objects.filter(pk=draft.pk).update(**values)

    def test_history_protects_registration_eligibility_and_preparing_actor_from_deletion(self):
        draft, _ = prepare_draft(actor=self.actor, event_id=self.event.pk, registration_id=self.registration.pk, reason="Teste")
        for obj in (self.registration, draft.eligibility, self.actor):
            with self.subTest(model=obj._meta.label), self.assertRaises(ProtectedError):
                obj.delete()
        self.assertTrue(CertificateDraft.objects.filter(pk=draft.pk).exists())

    def test_service_rejects_empty_reason_even_without_api_form(self):
        for reason in (" ", "x" * 1001):
            with self.assertRaises(ValidationError):
                prepare_draft(actor=self.actor, event_id=self.event.pk, registration_id=self.registration.pk, reason=reason)
        self.assertFalse(CertificateEligibility.objects.exists())


class CertificateConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actors = [factories.user("operator1@example.org", is_staff=True), factories.user("operator2@example.org", is_staff=True)]
        for actor in self.actors:
            grant(actor, cancel=True)
        self.owner = factories.user()
        self.event = factories.event()
        self.price = factories.price(self.event)
        self.registration, _ = register(user=self.owner, event_id=self.event.pk, price_id=self.price.pk)

    def concurrent(self, operations):
        barrier = Barrier(len(operations))
        def run(operation):
            try:
                barrier.wait(timeout=10)
                return operation()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(operations)) as executor:
            return list(executor.map(run, operations))

    def prepare(self, actor):
        return prepare_draft(actor=actor, event_id=self.event.pk, registration_id=self.registration.pk, reason="Preparação concorrente")

    def test_different_operators_prepare_exactly_one_draft_and_one_audit_entry(self):
        results = self.concurrent([lambda: self.prepare(self.actors[0]), lambda: self.prepare(self.actors[1])])
        self.assertCountEqual([created for _, created in results], [True, False])
        self.assertEqual(results[0][0].pk, results[1][0].pk)
        self.assertEqual(CertificateEligibility.objects.count(), 1)
        self.assertEqual(CertificateDraft.objects.count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action="certificate.draft_prepared").count(), 1)

    def test_concurrent_cancel_never_reopens_or_approves_registration(self):
        def prepare():
            try:
                self.prepare(self.actors[0])
                return "prepared"
            except ValidationError as error:
                return error.code
        def cancel():
            cancel_registration(actor=User.objects.get(pk=self.actors[1].pk), registration_id=self.registration.pk, reason="Cancelamento concorrente")
            return "cancelled"
        results = self.concurrent([prepare, cancel])
        self.assertIn(results[0], ("prepared", "registration_cancelled"))
        self.assertEqual(results[1], "cancelled")
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "cancelled")
        self.assertFalse(CertificateEligibility.objects.exclude(decision="pending").exists())
        self.assertEqual(CertificateDraft.objects.count(), int(results[0] == "prepared"))
