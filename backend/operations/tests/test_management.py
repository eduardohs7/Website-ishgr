import csv
import io
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounts.models import User
from operations.models import AuditEntry
from operations.views import csv_cell
from registrations.models import Registration
from registrations.services import cancel_registration, register, review_category
from registrations.tests import factories


class ManagementTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.participant = factories.user()
        cls.operator = factories.user("operator@example.org", is_staff=True)
        cls.event = factories.event()
        cls.price = factories.price(cls.event, requires_review=True)
        cls.registration, _ = register(user=cls.participant, event_id=cls.event.pk, price_id=cls.price.pk)

    def grant(self, *codes):
        self.operator.user_permissions.add(*Permission.objects.filter(content_type__app_label="registrations", codename__in=codes))
        self.operator = User.objects.get(pk=self.operator.pk)
        self.client.force_login(self.operator)

    def detail(self):
        return reverse("operations:registration", args=[self.registration.pk])

    def test_staff_flag_alone_does_not_authorize_operations(self):
        self.client.force_login(self.operator)
        for route in (reverse("operations:registrations"), self.detail(), reverse("operations:indicators"), reverse("operations:exports")):
            self.assertEqual(self.client.get(route).status_code, 403)

    def test_nonstaff_with_permissions_still_denied(self):
        self.grant("view_registration", "review_registration")
        self.operator.is_staff = False
        self.operator.save(update_fields=["is_staff"])
        self.assertEqual(self.client.get(self.detail()).status_code, 403)
        with self.assertRaises(PermissionDenied):
            review_category(actor=self.operator, registration_id=self.registration.pk, decision="approved", reason="Teste")

    def test_indicator_role_sees_aggregates_without_personal_details(self):
        self.grant("view_registration_indicators")
        response = self.client.get(reverse("operations:indicators"))
        self.assertContains(response, "123,45")
        self.assertNotContains(response, self.participant.email)
        self.assertNotContains(response, self.participant.full_name)
        self.assertEqual(self.client.get(self.detail()).status_code, 403)
        self.assertEqual(self.client.get(reverse("operations:exports")).status_code, 403)

    def test_view_permission_cannot_review_cancel_export_or_manage_accounts(self):
        self.grant("view_registration")
        self.assertEqual(self.client.get(self.detail()).status_code, 200)
        self.client.post(self.detail(), {"action": "approved", "reason": "Tentativa sem permissão"})
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.category_review, "pending")
        with self.assertRaises(PermissionDenied):
            cancel_registration(actor=self.operator, registration_id=self.registration.pk, reason="Sem permissão")
        self.assertEqual(self.client.get(reverse("operations:exports")).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:accounts_user_change", args=[self.participant.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:registrations_event_change", args=[self.event.pk])).status_code, 403)

    def test_review_is_audited_reason_required_and_never_confirms_payment(self):
        self.grant("view_registration", "review_registration")
        with self.assertRaises(ValidationError):
            review_category(actor=self.operator, registration_id=self.registration.pk, decision="approved", reason="  ")
        response = self.client.post(self.detail(), {"action": "approved", "reason": "Categoria revisada pela comissão"})
        self.assertRedirects(response, self.detail())
        self.registration.refresh_from_db()
        self.assertEqual((self.registration.category_review, self.registration.status), ("approved", "pending"))
        entry = AuditEntry.objects.get(action="registration.category_reviewed")
        self.assertEqual(entry.actor, self.operator)
        self.assertEqual(entry.reason, "Categoria revisada pela comissão")
        review_category(actor=self.operator, registration_id=self.registration.pk, decision="approved", reason="Repetição")
        self.assertEqual(AuditEntry.objects.filter(action="registration.category_reviewed").count(), 1)

    def test_rejected_category_can_be_reviewed_again_with_history(self):
        self.grant("view_registration", "review_registration")
        for decision in ("rejected", "approved"):
            review_category(actor=self.operator, registration_id=self.registration.pk, decision=decision, reason="Revisão fictícia")
        self.assertEqual(AuditEntry.objects.filter(action="registration.category_reviewed").count(), 2)

    def test_cancelled_registration_cannot_be_reactivated_by_retry_or_review(self):
        self.grant("view_registration", "review_registration", "cancel_registration")
        cancel_registration(actor=self.operator, registration_id=self.registration.pk, reason="Solicitação fictícia")
        cancel_registration(actor=self.operator, registration_id=self.registration.pk, reason="Repetição")
        with self.assertRaises(ValidationError):
            review_category(actor=self.operator, registration_id=self.registration.pk, decision="approved", reason="Tentativa")
        retried, created = register(user=self.participant, event_id=self.event.pk, price_id=self.price.pk)
        self.assertFalse(created)
        self.assertEqual(retried.status, "cancelled")
        self.assertEqual(AuditEntry.objects.filter(action="registration.cancelled").count(), 1)
        self.assertEqual(retried.cancelled_by, self.operator)
        self.assertIsNotNone(retried.cancelled_at)

    def test_invalid_review_decision_or_nonrequired_review_rejected(self):
        self.grant("view_registration", "review_registration")
        with self.assertRaises(ValidationError):
            review_category(actor=self.operator, registration_id=self.registration.pk, decision="confirmed", reason="Tentativa")
        other_event = factories.event("other")
        other_price = factories.price(other_event)
        other, _ = register(user=self.participant, event_id=other_event.pk, price_id=other_price.pk)
        with self.assertRaises(ValidationError):
            review_category(actor=self.operator, registration_id=other.pk, decision="approved", reason="Tentativa")

    def test_database_requires_cancel_actor_reason_and_date(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Registration.objects.filter(pk=self.registration.pk).update(status="cancelled")

    def test_audit_failure_rolls_back_registration_and_admin_change(self):
        self.grant("view_registration", "cancel_registration")
        with patch("registrations.services.audit", side_effect=RuntimeError("Synthetic database failure")):
            with self.assertRaises(RuntimeError):
                cancel_registration(actor=self.operator, registration_id=self.registration.pk, reason="Teste")
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "pending")
        other = factories.user("other@example.org")
        with patch("registrations.services.audit", side_effect=RuntimeError("Synthetic database failure")):
            with self.assertRaises(RuntimeError):
                register(user=other, event_id=self.event.pk, price_id=self.price.pk)
        self.assertFalse(Registration.objects.filter(user=other).exists())

    def test_management_post_requires_csrf_and_get_cannot_cancel(self):
        self.grant("view_registration", "cancel_registration", "export_registration")
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.operator)
        self.assertEqual(client.post(self.detail(), {"action": "cancel", "reason": "Teste"}).status_code, 403)
        self.assertEqual(client.post(reverse("operations:exports"), {}).status_code, 403)
        self.client.get(self.detail(), {"action": "cancel", "reason": "Teste"})
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "pending")

    def test_search_filters_and_invalid_filter_do_not_expand_export(self):
        self.grant("view_registration", "export_registration")
        response = self.client.get(reverse("operations:registrations"), {"q": self.participant.email, "status": "pending"})
        self.assertEqual(response.context["page"].paginator.count, 1)
        response = self.client.get(reverse("operations:registrations"), {"status": "confirmed"})
        self.assertEqual(response.context["page"].paginator.count, 0)
        response = self.client.post(reverse("operations:exports"), {"status": "invalid"})
        self.assertEqual(response["Content-Type"], "text/html; charset=utf-8")
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    def test_filtered_csv_neutralizes_formulas_and_is_audited_without_search_pii(self):
        self.grant("view_registration", "export_registration")
        self.participant.full_name = " \t=HYPERLINK(\"https://example.org\",\"teste\")"
        self.participant.save(update_fields=["full_name"])
        url = reverse("operations:exports")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())
        response = self.client.post(url, {"q": self.participant.email, "status": "pending"})
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("no-store", response["Cache-Control"])
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[1][1].startswith("'"))
        self.assertEqual(rows[1][2], self.participant.email)
        self.assertEqual(rows[1][6], "123.45")
        entry = AuditEntry.objects.get(action="registration.exported")
        self.assertEqual(entry.details["rows"], 1)
        self.assertTrue(entry.details["search_applied"])
        self.assertNotIn(self.participant.email, str(entry.details))

    def test_csv_controls_and_formula_prefixes_are_sanitized(self):
        for text in ["=1", "+1", "-1", "@SUM(1)", " \t=1", "\ufeff=1", "\x1f=1", "\u2003=1"]:
            self.assertTrue(csv_cell(text).startswith("'"), repr(text))
        self.assertEqual(csv_cell("Nome, com vírgula"), "Nome, com vírgula")
        self.assertEqual(csv_cell("linha\nseguinte"), "linha seguinte")

    @override_settings(REGISTRATION_EXPORT_MAX_ROWS=0)
    def test_export_limit_requires_narrower_filters(self):
        self.grant("view_registration", "export_registration")
        response = self.client.post(reverse("operations:exports"), {})
        self.assertEqual(response.status_code, 413)
        self.assertFalse(AuditEntry.objects.filter(action="registration.exported").exists())

    def test_export_audit_failure_prevents_delivery(self):
        self.grant("view_registration", "export_registration")
        with patch("operations.views.audit", side_effect=RuntimeError("Synthetic database failure")):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse("operations:exports"), {})

    def test_roles_command_is_additive_and_never_creates_users(self):
        before = User.objects.count()
        group = Group.objects.create(name="CHAGS — Atendimento")
        extra = Permission.objects.get(content_type__app_label="registrations", codename="view_registration_indicators")
        group.permissions.add(extra)
        for _ in range(2):
            call_command("setup_registration_roles", stdout=io.StringIO())
        self.assertEqual(User.objects.count(), before)
        self.assertEqual(Group.objects.count(), 3)
        self.assertTrue(group.permissions.filter(pk=extra.pk).exists())
        self.assertFalse(group.permissions.filter(codename="export_registration").exists())

    def test_superuser_admin_registration_and_audit_are_readonly(self):
        self.operator.is_superuser = True
        self.operator.save(update_fields=["is_superuser"])
        self.client.force_login(self.operator)
        url = reverse("admin:registrations_registration_change", args=[self.registration.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url, {"amount": "0", "status": "confirmed"}).status_code, 403)
        self.assertEqual(self.client.get(reverse("admin:registrations_registration_add")).status_code, 403)
        audit_entry = AuditEntry.objects.first()
        audit_url = reverse("admin:operations_auditentry_change", args=[audit_entry.pk])
        self.assertEqual(self.client.get(audit_url).status_code, 200)
        self.assertEqual(self.client.post(audit_url, {"reason": "Alterar histórico"}).status_code, 403)

    def test_admin_index_links_and_registration_list_for_delegated_staff(self):
        self.grant("view_registration")
        response = self.client.get(reverse("admin:index"))
        self.assertContains(response, reverse("operations:registrations"))
        self.assertNotContains(response, reverse("operations:exports"))
        self.assertEqual(self.client.get(reverse("admin:registrations_registration_changelist")).status_code, 200)

    def test_technical_admin_can_replace_price_without_mutating_existing_amount(self):
        self.operator.is_superuser = True
        self.operator.save(update_fields=["is_superuser"])
        self.client.force_login(self.operator)
        url = reverse("admin:registrations_registrationprice_change", args=[self.price.pk])
        response = self.client.post(url, {"amount": "0", "active": "", "valid_until_0": "", "valid_until_1": "", "_save": "Salvar"})
        self.assertEqual(response.status_code, 302)
        self.price.refresh_from_db()
        self.assertEqual(self.price.amount, Decimal("123.45"))
        self.assertFalse(self.price.active)
        entry = AuditEntry.objects.get(action="configuration.updated")
        self.assertIn("active", entry.details["fields"])
