from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.db import connections
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
import pyotp

from accounts import mfa
from accounts.models import StaffMFA
from operations.models import AuditEntry
from registrations.tests import factories
from .test_mfa import LIMITS, TEST_SECRET


@override_settings(STAFF_MFA_REQUIRED=False, ACCOUNT_RATE_LIMITS=LIMITS)
class MFAConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.actor = factories.user("operator@example.org", is_staff=True)
        self.factor = StaffMFA.objects.create(user=self.actor, enabled=True,
            secret_ciphertext=mfa.cipher().encrypt(TEST_SECRET.encode()).decode())

    def concurrent(self, payload):
        clients = [Client(), Client()]
        for client in clients:
            client.force_login(self.actor)
        barrier = Barrier(2)
        def run(client):
            try:
                barrier.wait(timeout=10)
                return client.post(reverse("staff_mfa:verify"), payload, content_type="application/json").status_code
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            return list(executor.map(run, clients))

    def test_simultaneous_totp_consumption_grants_only_one_session(self):
        now = timezone.now()
        with patch("accounts.mfa.timezone.now", return_value=now):
            results = self.concurrent({"code": pyotp.TOTP(TEST_SECRET).at(now)})
        self.assertCountEqual(results, [200, 401])
        self.assertEqual(AuditEntry.objects.filter(action="security.mfa_verified").count(), 1)

    def test_simultaneous_recovery_code_consumption_grants_only_one_session(self):
        code = mfa.new_recovery_codes(self.factor)[0]
        self.assertCountEqual(self.concurrent({"code": code, "method": "recovery"}), [200, 401])
        self.assertEqual(self.factor.recovery_codes.filter(used_at__isnull=False).count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action="security.mfa_verified").count(), 1)
