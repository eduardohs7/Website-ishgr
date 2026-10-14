from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.db import connections
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse

from accounts.models import AccountEmail
from accounts.services import consume_token, request_account_email
from accounts.tests.test_security_api import LIMITS, NEW_PASSWORD, PASSWORD
from operations.models import AuditEntry
from registrations.tests import factories


@override_settings(STAFF_MFA_REQUIRED=False, ACCOUNT_RATE_LIMITS=LIMITS)
class SecurityConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = factories.user()
        self.clients = [Client(), Client()]
        for client in self.clients:
            client.force_login(self.owner)

    def concurrent(self, operations):
        barrier = Barrier(2)
        def run(operation):
            try:
                barrier.wait(timeout=10)
                return operation()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(run, operations))

    def change(self, index, password=NEW_PASSWORD):
        return self.clients[index].post(reverse("auth_api:change_password"), {
            "current_password": PASSWORD, "new_password1": password, "new_password2": password,
        }, content_type="application/json").status_code

    def revoke(self, index):
        return self.clients[index].post(reverse("auth_api:logout_all"), {"current_password": PASSWORD}, content_type="application/json").status_code

    def test_competing_password_changes_cannot_overwrite_each_other(self):
        other_password = "Synthetic-competing-password-48!"
        results = self.concurrent([lambda: self.change(0), lambda: self.change(1, other_password)])
        self.assertCountEqual(results, [200, 401])
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(NEW_PASSWORD if results[0] == 200 else other_password))
        self.assertEqual(AuditEntry.objects.filter(action="security.password_changed").count(), 1)

    def test_global_logout_and_password_change_serialize_using_the_same_account_lock(self):
        results = self.concurrent([lambda: self.change(0), lambda: self.revoke(1)])
        self.assertCountEqual(results, [200, 401])
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(NEW_PASSWORD if results[0] == 200 else PASSWORD))
        self.assertEqual(AuditEntry.objects.filter(action__in=["security.password_changed", "security.sessions_revoked"]).count(), 1)

    def test_two_global_logouts_cannot_reauthorize_a_stale_session(self):
        results = self.concurrent([lambda: self.revoke(0), lambda: self.revoke(1)])
        self.assertCountEqual(results, [200, 401])
        for client in self.clients:
            self.assertFalse(client.get(reverse("auth_api:session")).json()["data"]["authenticated"])
        self.assertEqual(AuditEntry.objects.filter(action="security.sessions_revoked").count(), 1)

    def test_reset_link_and_authenticated_change_cannot_both_update_password(self):
        request_account_email(self.owner.email, "reset")
        raw = AccountEmail.objects.get().raw_token
        reset_password = "Synthetic-reset-concurrent-password-98!"
        results = self.concurrent([lambda: self.change(0), lambda: consume_token(raw, "reset", password=reset_password)])
        self.assertIn(results, ([200, False], [401, True]))
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(NEW_PASSWORD if results[0] == 200 else reset_password))
