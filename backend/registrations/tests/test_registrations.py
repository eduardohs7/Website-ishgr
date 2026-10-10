from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models.deletion import ProtectedError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from operations.models import AuditEntry
from registrations.models import Event, Registration, RegistrationPrice
from registrations.services import register
from . import factories


class RegistrationTests(TestCase):
    def setUp(self):
        self.user = factories.user()
        self.event = factories.event()
        self.price = factories.price(self.event, requires_review=True)
        self.client.force_login(self.user)

    def register(self, **kwargs):
        return register(user=self.user, event_id=self.event.pk, price_id=kwargs.pop("price_id", self.price.pk), **kwargs)

    def test_creation_snapshot_review_and_idempotent_duplicate(self):
        obj, created = self.register()
        other, retried = self.register(price_id="invalid-after-first-success")
        self.assertTrue(created)
        self.assertFalse(retried)
        self.assertEqual(obj.pk, other.pk)
        self.assertEqual((obj.amount, obj.currency, obj.category_name), (Decimal("123.45"), "BRL", "Categoria fictícia"))
        self.assertEqual((obj.status, obj.category_review), ("pending", "pending"))
        self.assertEqual(AuditEntry.objects.filter(action="registration.created").count(), 1)

    def test_signup_post_ignores_owner_price_and_status_injection(self):
        other = factories.user("other@example.org")
        response = self.client.post(reverse("registrations:mine"), {
            "price": self.price.pk, "user": other.pk, "amount": "0", "currency": "USD", "status": "confirmed", "category_review": "approved",
        })
        self.assertRedirects(response, reverse("registrations:mine"))
        obj = Registration.objects.get()
        self.assertEqual(obj.user, self.user)
        self.assertEqual(obj.amount, self.price.amount)
        self.assertEqual(obj.status, "pending")

    def test_participant_only_sees_own_registration(self):
        self.register()
        other = factories.user("other@example.org")
        self.client.force_login(other)
        response = self.client.get(reverse("registrations:mine"), {"user": self.user.pk})
        self.assertIsNone(response.context["registration"])
        self.assertNotContains(response, self.user.email)
        self.assertEqual(self.client.get(f"/participante/inscricao/{Registration.objects.get().pk}/").status_code, 404)

    def test_csrf_required_to_create_registration(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("registrations:mine"), {"price": self.price.pk}).status_code, 403)
        self.assertFalse(Registration.objects.exists())

    def test_unverified_or_inactive_cannot_register_even_through_service(self):
        for changes in ({"email_verified_at": None}, {"is_active": False}):
            with self.subTest(changes=changes):
                for field, value in changes.items():
                    setattr(self.user, field, value)
                self.user.save()
                with self.assertRaises(ValidationError):
                    self.register()
        self.assertFalse(Registration.objects.exists())

    def test_unverified_page_redirects_to_confirmation(self):
        self.user.email_verified_at = None
        self.user.save(update_fields=["email_verified_at"])
        self.assertRedirects(self.client.get(reverse("registrations:mine")), reverse("accounts:resend_confirmation"))

    def test_closed_window_or_deactivated_event_rejects(self):
        for enabled, opens, closes in [
            (False, timezone.now() - timedelta(days=1), timezone.now() + timedelta(days=1)),
            (True, timezone.now() + timedelta(days=1), timezone.now() + timedelta(days=2)),
            (True, timezone.now() - timedelta(days=2), timezone.now() - timedelta(days=1)),
        ]:
            self.event.registration_enabled = enabled
            self.event.registration_opens_at, self.event.registration_closes_at = opens, closes
            self.event.save()
            with self.assertRaises(ValidationError):
                self.register()
        self.assertFalse(Registration.objects.exists())

    def test_price_from_other_event_wrong_currency_or_inactive_rejected(self):
        other_price = factories.price(factories.event("other"))
        with self.assertRaises(ValidationError):
            self.register(price_id=other_price.pk)
        self.event.registration_currency = "USD"
        self.event.save()
        with self.assertRaises(ValidationError):
            self.register()
        self.event.registration_currency = "BRL"
        self.event.save()
        for target in (self.price, self.price.category):
            target.active = False
            target.save()
            with self.assertRaises(ValidationError):
                self.register()
            target.active = True
            target.save()

    def test_quote_expired_after_form_validation_rejected(self):
        now = timezone.now()
        RegistrationPrice.objects.filter(pk=self.price.pk).update(valid_until=now + timedelta(hours=1))
        with patch("registrations.services.timezone.now", return_value=now + timedelta(hours=2)):
            with self.assertRaises(ValidationError):
                self.register()
        self.assertFalse(Registration.objects.exists())

    def test_price_replacement_preserves_existing_snapshot(self):
        obj, _ = self.register()
        self.price.active = False
        self.price.save(update_fields=["active"])
        RegistrationPrice.objects.create(category=self.price.category, amount=Decimal("222.22"), currency="BRL", valid_from=timezone.now())
        self.price.category.name_pt = "Nova descrição"
        self.price.category.requires_review = False
        self.price.category.save()
        obj.refresh_from_db()
        self.assertEqual(obj.amount, Decimal("123.45"))
        self.assertEqual(obj.category_name, "Categoria fictícia")
        self.assertTrue(obj.review_required)

    def test_database_protects_snapshots_commercial_price_and_cross_event_category(self):
        obj, _ = self.register()
        other = factories.event("other")
        changes = [
            (Registration.objects.filter(pk=obj.pk), {"amount": Decimal("1.00")}),
            (Registration.objects.filter(pk=obj.pk), {"category_name": "Alterado"}),
            (Registration.objects.filter(pk=obj.pk), {"event": other}),
            (RegistrationPrice.objects.filter(pk=self.price.pk), {"amount": Decimal("1.00")}),
            (type(self.price.category).objects.filter(pk=self.price.category.pk), {"event": other}),
        ]
        for queryset, fields in changes:
            with self.subTest(fields=fields), self.assertRaises(IntegrityError), transaction.atomic():
                queryset.update(**fields)

    def test_database_rejects_invalid_snapshot_at_creation_and_duplicate(self):
        fields = dict(user=self.user, event=self.event, category=self.price.category, source_price=self.price,
                      amount=self.price.amount, currency=self.price.currency, category_name=self.price.category.name_pt,
                      review_required=True, category_review="pending")
        for invalid in ({"amount": Decimal("0")}, {"status": "confirmed"}, {"category_review": "approved"}):
            with self.subTest(invalid=invalid), self.assertRaises(IntegrityError), transaction.atomic():
                Registration.objects.create(**(fields | invalid))
        self.register()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Registration.objects.create(**fields)

    def test_overlapping_price_blocked_but_adjacent_and_other_currency_allowed(self):
        self.price.valid_until = timezone.now() + timedelta(hours=1)
        self.price.save(update_fields=["valid_until"])
        data = dict(category=self.price.category, amount=Decimal("11.11"), currency="BRL", valid_from=timezone.now())
        with self.assertRaises(IntegrityError), transaction.atomic():
            RegistrationPrice.objects.create(**data)
        RegistrationPrice.objects.create(**(data | {"valid_from": self.price.valid_until}))
        RegistrationPrice.objects.create(**(data | {"currency": "USD"}))

    def test_invalid_money_or_windows_rejected_by_database(self):
        data = dict(category=self.price.category, amount=Decimal("11.11"), currency="EUR", valid_from=timezone.now())
        for invalid in ({"amount": Decimal("-0.01")}, {"currency": "XYZ"}, {"valid_until": data["valid_from"]}):
            with self.subTest(invalid=invalid), self.assertRaises(IntegrityError), transaction.atomic():
                RegistrationPrice.objects.create(**(data | invalid))
        with self.assertRaises(IntegrityError), transaction.atomic():
            Event.objects.filter(pk=self.event.pk).update(registration_opens_at=None)

    def test_referenced_user_and_price_are_protected_from_deletion(self):
        self.register()
        for target in (self.user, self.price, self.price.category, self.event):
            with self.assertRaises(ProtectedError):
                target.delete()

    def test_zero_amount_does_not_grant_exemption_or_confirm_registration(self):
        self.price.active = False
        self.price.save(update_fields=["active"])
        zero = RegistrationPrice.objects.create(category=self.price.category, amount=Decimal("0.00"), currency="BRL", valid_from=timezone.now())
        obj, _ = self.register(price_id=zero.pk)
        self.assertEqual(obj.amount, Decimal("0.00"))
        self.assertEqual(obj.status, "pending")

    def test_unconfigured_event_page_and_initialization_are_safe_and_repeatable(self):
        self.price.delete()
        self.price.category.delete()
        Event.objects.all().delete()
        self.assertContains(self.client.get(reverse("registrations:mine")), "ainda não estão disponíveis")
        call_command("initialize_chags14", stdout=StringIO())
        configured = Event.objects.get(code="chags14")
        self.assertFalse(configured.registration_enabled)
        self.assertFalse(configured.categories.exists())
        configured.title = "Título preservado"
        configured.save()
        call_command("initialize_chags14", stdout=StringIO())
        configured.refresh_from_db()
        self.assertEqual(configured.title, "Título preservado")

    def test_jems_redirect_uses_only_safe_configured_url(self):
        url = reverse("registrations:jems")
        self.assertContains(self.client.get(url), "independente")
        self.event.jems_url = "https://jems.example.org/event/test"
        self.event.save()
        response = self.client.get(url, {"next": "https://attacker.example.org"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self.event.jems_url)
        for unsafe in ("http://jems.example.org", "https://user:pass@jems.example.org"):
            self.event.jems_url = unsafe
            self.event.save()
            self.assertEqual(self.client.get(url).status_code, 200)
