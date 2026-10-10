import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.db import connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import ParticipantProfile
from operations.models import AuditEntry
from registrations.models import Event, Registration, RegistrationCategory, RegistrationPrice
from registrations.services import cancel_registration, register
from . import factories


class ParticipantRegistrationApiTests(TestCase):
    def setUp(self):
        self.user = factories.user()
        ParticipantProfile.objects.create(user=self.user, preferred_language="en")
        self.event = factories.event()
        self.price = factories.price(self.event, requires_review=True)
        self.price.category.name_en = "Synthetic category"
        self.price.category.name_es = "Categoría ficticia"
        self.price.category.save()
        self.client.force_login(self.user)

    def post(self, payload, client=None, **extra):
        return (client or self.client).post(reverse("participant_api:registration"), payload, content_type="application/json", **extra)

    def get(self, route, **extra):
        return self.client.get(reverse(f"participant_api:{route}"), **extra)

    def make_price(self, *, amount="456.78", currency="BRL", category_active=True, **extra):
        category = RegistrationCategory.objects.create(
            event=self.event, code="category-" + uuid.uuid4().hex, name_pt="Categoria adicional", active=category_active,
        )
        return RegistrationPrice.objects.create(category=category, amount=Decimal(amount), currency=currency,
                                                valid_from=extra.pop("valid_from", timezone.now() - timedelta(hours=1)), **extra)

    def test_event_metadata_reports_dates_currency_and_current_window(self):
        response = self.get("event")
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]["event"]
        self.assertEqual(data["id"], str(self.event.pk))
        self.assertEqual(data["starts_on"], "2027-07-12")
        self.assertEqual(data["ends_on"], "2027-07-16")
        self.assertEqual(data["registration_currency"], "BRL")
        self.assertTrue(data["registrations_open"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertNotIn("jems_url", data)

    def test_prices_are_decimal_strings_and_use_saved_participant_language(self):
        response = self.get("prices", HTTP_ACCEPT_LANGUAGE="es")
        self.assertEqual(response["Content-Language"], "en")
        data = response.json()["data"]
        self.assertEqual(len(data["prices"]), 1)
        price = data["prices"][0]
        self.assertEqual(price["price_id"], str(self.price.pk))
        self.assertEqual(price["category_name"], "Synthetic category")
        self.assertEqual(price["amount"], "123.45")
        self.assertEqual(price["currency"], "BRL")
        self.assertTrue(price["requires_review"])

    def test_catalog_filters_category_price_currency_and_other_events(self):
        self.make_price(active=False)
        self.make_price(category_active=False)
        self.make_price(currency="USD")
        factories.price(factories.event("another-event"))
        data = self.get("prices").json()["data"]
        self.assertEqual([item["price_id"] for item in data["prices"]], [str(self.price.pk)])

    def test_catalog_respects_inclusive_start_and_exclusive_end(self):
        at = timezone.now()
        starts_now = self.make_price(valid_from=at, valid_until=at + timedelta(hours=1))
        self.make_price(valid_from=at + timedelta(seconds=1))
        self.make_price(valid_until=at)
        with patch("registrations.api.timezone.now", return_value=at):
            data = self.get("prices").json()["data"]
        self.assertCountEqual([item["price_id"] for item in data["prices"]], [str(self.price.pk), str(starts_now.pk)])

    def test_closed_event_returns_empty_catalog_and_rejects_new_registration(self):
        self.event.registration_enabled = False
        self.event.save(update_fields=["registration_enabled"])
        data = self.get("prices").json()["data"]
        self.assertFalse(data["registrations_open"])
        self.assertEqual(data["prices"], [])
        response = self.post({"price_id": str(self.price.pk)})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "registrations_closed")
        self.assertFalse(Registration.objects.exists())

    def test_future_and_closed_at_boundary_windows_are_rechecked_on_submit(self):
        at = timezone.now()
        for opens, closes in [(at + timedelta(seconds=1), at + timedelta(hours=1)), (at - timedelta(hours=1), at)]:
            self.event.registration_opens_at, self.event.registration_closes_at = opens, closes
            self.event.save(update_fields=["registration_opens_at", "registration_closes_at"])
            with patch("registrations.services.timezone.now", return_value=at):
                response = self.post({"price_id": str(self.price.pk)})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "registrations_closed")

    def test_registration_creation_and_duplicate_are_idempotent_and_audited_once(self):
        response = self.post({"price_id": str(self.price.pk)})
        self.assertEqual(response.status_code, 201)
        data = response.json()["data"]
        self.assertTrue(data["created"])
        own = data["registration"]
        self.assertEqual(own["amount"], "123.45")
        self.assertEqual(own["status"], "pending")
        self.assertEqual(own["category_review"], "pending")
        self.assertEqual(own["category_name"], "Synthetic category")
        self.assertNotIn("user", own)
        self.assertNotIn("cancelled_by", own)
        again = self.post({"price_id": str(uuid.uuid4())})
        self.assertEqual(again.status_code, 200)
        self.assertFalse(again.json()["data"]["created"])
        self.assertEqual(again.json()["data"]["registration"]["id"], own["id"])
        self.assertEqual(Registration.objects.count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action="registration.created").count(), 1)

    def test_foreign_owner_and_payment_state_fields_are_rejected(self):
        other = factories.user("other@example.org")
        for field, value in {"user": str(other.pk), "amount": "0", "currency": "USD", "status": "confirmed", "category_review": "approved", "event_id": str(self.event.pk)}.items():
            response = self.post({"price_id": str(self.price.pk), field: value})
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "unknown_fields")
        self.assertFalse(Registration.objects.exists())
        self.assertFalse(AuditEntry.objects.exists())

    def test_unknown_price_and_price_of_other_event_are_unavailable(self):
        other = factories.price(factories.event("other"))
        for price_id in (uuid.uuid4(), other.pk):
            response = self.post({"price_id": str(price_id)})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "price_unavailable")
        for payload in ({}, {"price_id": "not-a-uuid"}):
            response = self.post(payload)
            self.assertEqual(response.status_code, 422)
            self.assertIn("price_id", response.json()["error"]["fields"])
        self.assertFalse(Registration.objects.exists())

    def test_catalog_price_is_revalidated_after_deactivation(self):
        self.assertEqual(len(self.get("prices").json()["data"]["prices"]), 1)
        RegistrationCategory.objects.filter(pk=self.price.category_id).update(active=False)
        response = self.post({"price_id": str(self.price.pk)})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "price_unavailable")
        self.assertFalse(Registration.objects.exists())

    def test_price_in_wrong_currency_and_expired_price_cannot_be_selected(self):
        usd = self.make_price(currency="USD")
        expired = self.make_price(valid_until=timezone.now())
        for price in (usd, expired):
            response = self.post({"price_id": str(price.pk)})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "price_unavailable")

    def test_get_registration_only_returns_owner_for_current_event(self):
        own, _ = register(user=self.user, event_id=self.event.pk, price_id=self.price.pk)
        other = factories.user("other@example.org")
        other_registration, _ = register(user=other, event_id=self.event.pk, price_id=self.price.pk)
        response = self.client.get(reverse("participant_api:registration"), {"user": str(other.pk), "id": str(other_registration.pk)})
        self.assertEqual(response.json()["data"]["registration"]["id"], str(own.pk))
        self.assertNotContains(response, str(other_registration.pk))
        self.client.force_login(other)
        self.assertEqual(self.get("registration").json()["data"]["registration"]["id"], str(other_registration.pk))
        fresh = factories.user("fresh@example.org")
        self.client.force_login(fresh)
        self.assertIsNone(self.get("registration").json()["data"]["registration"])

    def test_get_filters_out_registration_for_another_event(self):
        other = factories.event("other")
        price = factories.price(other)
        register(user=self.user, event_id=other.pk, price_id=price.pk)
        self.assertIsNone(self.get("registration").json()["data"]["registration"])

    def test_cancelled_registration_remains_visible_and_cannot_be_reactivated(self):
        own, _ = register(user=self.user, event_id=self.event.pk, price_id=self.price.pk)
        actor = factories.user("operator@example.org", is_staff=True, is_superuser=True)
        cancel_registration(actor=actor, registration_id=own.pk, reason="Pedido fictício")
        self.event.registration_enabled = False
        self.event.save(update_fields=["registration_enabled"])
        self.assertEqual(self.get("registration").json()["data"]["registration"]["status"], "cancelled")
        response = self.post({"price_id": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["registration"]["status"], "cancelled")
        self.assertFalse(response.json()["data"]["created"])
        self.assertEqual(AuditEntry.objects.filter(action="registration.created").count(), 1)

    def test_category_name_is_frozen_and_localized_from_registration_snapshot(self):
        register(user=self.user, event_id=self.event.pk, price_id=self.price.pk)
        RegistrationCategory.objects.filter(pk=self.price.category_id).update(name_en="Modified live name", name_es="Nombre cambiado")
        self.assertEqual(self.get("registration").json()["data"]["registration"]["category_name"], "Synthetic category")
        ParticipantProfile.objects.filter(user=self.user).update(preferred_language="es")
        response = self.get("registration")
        self.assertEqual(response["Content-Language"], "es")
        self.assertEqual(response.json()["data"]["registration"]["category_name"], "Categoría ficticia")
        self.assertEqual(response.json()["data"]["registration"]["status_label"], "Pendiente de requisitos")

    def test_missing_translation_falls_back_to_frozen_portuguese(self):
        RegistrationCategory.objects.filter(pk=self.price.category_id).update(name_en="")
        self.assertEqual(self.get("prices").json()["data"]["prices"][0]["category_name"], "Categoria fictícia")
        register(user=self.user, event_id=self.event.pk, price_id=self.price.pk)
        self.assertEqual(self.get("registration").json()["data"]["registration"]["category_name"], "Categoria fictícia")

    def test_zero_amount_does_not_confirm_or_skip_category_review(self):
        price = self.make_price(amount="0")
        response = self.post({"price_id": str(price.pk)})
        own = response.json()["data"]["registration"]
        self.assertEqual(own["amount"], "0.00")
        self.assertEqual(own["status"], "pending")
        self.assertEqual(own["category_review"], "not_required")

    @override_settings(REGISTRATION_EVENT_CODE="not-configured")
    def test_missing_event_has_explicit_error_without_mutation(self):
        for route in ("event", "prices", "registration"):
            response = self.get(route)
            self.assertEqual(response.status_code, 404)
            self.assertEqual(response.json()["error"]["code"], "event_not_configured")
        self.assertEqual(self.post({"price_id": str(self.price.pk)}).status_code, 404)
        self.assertFalse(Registration.objects.exists())

    def test_csrf_is_required_to_create_registration(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response = self.post({"price_id": str(self.price.pk)}, client=client)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "csrf_failed")
        token = client.get(reverse("auth_api:session")).json()["data"]["csrf_token"]
        response = self.post({"price_id": str(self.price.pk)}, client=client, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 201)

    def test_participant_has_no_api_to_cancel_or_confirm_registration(self):
        for method in ("patch", "delete", "put"):
            response = getattr(self.client, method)(reverse("participant_api:registration"), {}, content_type="application/json")
            self.assertEqual(response.status_code, 405)
            self.assertEqual(response["Allow"], "GET, POST")
        self.assertEqual(self.client.post(reverse("participant_api:prices"), {}, content_type="application/json").status_code, 405)

    def test_creation_rolls_back_if_audit_fails(self):
        with patch("registrations.services.audit", side_effect=RuntimeError("Synthetic audit failure")):
            with self.assertRaises(RuntimeError):
                self.post({"price_id": str(self.price.pk)})
        self.assertFalse(Registration.objects.exists())
        self.assertFalse(AuditEntry.objects.exists())


class ParticipantRegistrationApiConcurrencyTests(TransactionTestCase):
    def test_simultaneous_http_submissions_create_one_registration_and_audit(self):
        user = factories.user()
        event = factories.event()
        price = factories.price(event)
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(user)
        barrier = Barrier(2)

        def submit(client):
            try:
                barrier.wait(timeout=10)
                response = client.post(reverse("participant_api:registration"), {"price_id": str(price.pk)}, content_type="application/json")
                return response.status_code, response.json()["data"]
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(submit, clients))
        self.assertCountEqual([status for status, _ in results], [201, 200])
        self.assertEqual(results[0][1]["registration"]["id"], results[1][1]["registration"]["id"])
        self.assertEqual(Registration.objects.count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action="registration.created").count(), 1)
