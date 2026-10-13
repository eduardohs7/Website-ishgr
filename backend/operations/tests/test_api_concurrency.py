from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import connections
from django.test import Client, TransactionTestCase
from django.urls import reverse

from operations.models import AuditEntry
from registrations.services import register
from registrations.tests import factories


class ManagementApiConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actor = factories.user("operator@example.org", is_staff=True, is_superuser=True)
        owner = factories.user()
        event = factories.event()
        price = factories.price(event, requires_review=True)
        self.registration, _ = register(user=owner, event_id=event.pk, price_id=price.pk)

    def concurrent_requests(self, operations):
        clients = [Client() for _ in operations]
        for client in clients:
            client.force_login(self.actor)
        barrier = Barrier(2)
        def run(item):
            client, (name, data) = item
            try:
                barrier.wait(timeout=10)
                response = client.post(reverse("management_api:" + name, kwargs={"registration_id": str(self.registration.pk)}),
                                       data, content_type="application/json")
                return response.status_code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(run, zip(clients, operations)))

    def test_two_simultaneous_cancellations_return_success_and_one_audit(self):
        self.assertEqual(self.concurrent_requests([("cancel", {"reason": "Teste"})] * 2), [200, 200])
        self.assertEqual(AuditEntry.objects.filter(action="registration.cancelled").count(), 1)
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "cancelled")

    def test_two_equal_reviews_create_only_one_audit_and_keep_pending_status(self):
        self.assertEqual(self.concurrent_requests([("review", {"decision": "approved", "reason": "Teste"})] * 2), [200, 200])
        self.assertEqual(AuditEntry.objects.filter(action="registration.category_reviewed").count(), 1)
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.category_review, "approved")
        self.assertEqual(self.registration.status, "pending")

    def test_cancellation_and_review_race_preserves_consistent_state(self):
        cancel, review = self.concurrent_requests([("cancel", {"reason": "Teste"}),
                                                   ("review", {"decision": "approved", "reason": "Teste"})])
        self.assertEqual(cancel, 200)
        self.assertIn(review, (200, 409))
        self.assertEqual(AuditEntry.objects.filter(action="registration.cancelled").count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action="registration.category_reviewed").count(), int(review == 200))
        self.registration.refresh_from_db()
        self.assertEqual(self.registration.status, "cancelled")
        self.assertEqual(self.registration.category_review, "approved" if review == 200 else "pending")
