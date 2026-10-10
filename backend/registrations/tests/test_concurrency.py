from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

from django.db import IntegrityError, connections
from django.test import TransactionTestCase
from django.utils import timezone

from operations.models import AuditEntry
from registrations.models import Registration, RegistrationPrice
from registrations.services import cancel_registration, register
from . import factories


class RegistrationConcurrencyTests(TransactionTestCase):
    def concurrent(self, fn):
        barrier = Barrier(2)

        def run(_):
            try:
                barrier.wait(timeout=10)
                return fn()
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(run, [1, 2]))

    def test_two_simultaneous_requests_create_one_registration_and_audit(self):
        user = factories.user()
        event = factories.event()
        price = factories.price(event)
        results = self.concurrent(lambda: register(user=user, event_id=event.pk, price_id=price.pk))
        self.assertCountEqual([created for _, created in results], [True, False])
        self.assertEqual(results[0][0].pk, results[1][0].pk)
        self.assertEqual(Registration.objects.count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action="registration.created").count(), 1)

    def test_database_rejects_one_of_two_overlapping_price_inserts(self):
        category = factories.price(factories.event()).category
        RegistrationPrice.objects.all().delete()

        def insert():
            try:
                RegistrationPrice.objects.create(category=category, amount=Decimal("99.99"), currency="BRL", valid_from=timezone.now())
            except IntegrityError:
                return "blocked"
            return "created"

        self.assertCountEqual(self.concurrent(insert), ["created", "blocked"])
        self.assertEqual(RegistrationPrice.objects.count(), 1)

    def test_simultaneous_cancellations_preserve_one_history_entry(self):
        user = factories.user()
        actor = factories.user("operator@example.org", is_staff=True, is_superuser=True)
        event = factories.event()
        price = factories.price(event)
        obj, _ = register(user=user, event_id=event.pk, price_id=price.pk)
        results = self.concurrent(lambda: cancel_registration(actor=actor, registration_id=obj.pk, reason="Solicitação fictícia"))
        self.assertEqual([item.status for item in results], ["cancelled", "cancelled"])
        self.assertEqual(AuditEntry.objects.filter(action="registration.cancelled").count(), 1)
