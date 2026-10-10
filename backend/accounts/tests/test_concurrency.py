from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
from threading import Barrier

from django.core import mail
from django.core.management import call_command
from django.db import connections
from django.test import Client, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.mail_delivery import claim_email
from accounts.models import AccountEmail, ParticipantProfile, User
from accounts.services import consume_token, register_participant, request_account_email


class AccountConcurrencyTests(TransactionTestCase):
    def concurrent(self, fn, values):
        barrier = Barrier(len(values))

        def run(value):
            try:
                barrier.wait(timeout=10)
                return fn(value)
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=len(values)) as executor:
            return list(executor.map(run, values))

    def test_concurrent_duplicate_signup_creates_one_account_profile_and_message(self):
        def register(email):
            register_participant(email=email, full_name="Pessoa", password="Synthetic-concurrent-password-47!")
        self.concurrent(register, ["person@example.org", "PERSON@EXAMPLE.ORG"])
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(ParticipantProfile.objects.count(), 1)
        self.assertEqual(AccountEmail.objects.count(), 1)

    def test_concurrent_reset_consumes_one_token_once(self):
        user = User.objects.create_user(
            "person@example.org", "Synthetic-old-password-47!", full_name="Pessoa", email_verified_at=timezone.now(),
        )
        request_account_email(user.email, "reset")
        raw = AccountEmail.objects.get().raw_token
        passwords = ["Synthetic-thread-A-password-47!", "Synthetic-thread-B-password-92!"]
        results = self.concurrent(lambda password: consume_token(raw, "reset", password=password), passwords)
        self.assertCountEqual(results, [True, False])
        user.refresh_from_db()
        self.assertTrue(user.check_password(passwords[results.index(True)]))
        self.assertFalse(user.check_password(passwords[results.index(False)]))

    def test_two_email_workers_cannot_claim_same_live_lease(self):
        user = User.objects.create_user("person@example.org", "Synthetic-password-47!", full_name="Pessoa")
        request_account_email(user.email, "confirm")
        results = self.concurrent(lambda _: claim_email(), [1, 2])
        self.assertEqual(sum(job is not None for job in results), 1)
        job = AccountEmail.objects.get()
        self.assertEqual(job.attempts, 1)
        AccountEmail.objects.filter(pk=job.pk).update(lease_until=timezone.now() - timedelta(seconds=1))
        call_command("send_account_emails", stdout=StringIO())
        job.refresh_from_db()
        self.assertEqual(job.status, "sent")
        self.assertEqual(job.attempts, 2)
        self.assertEqual(job.raw_token, "")
        self.assertEqual(len(mail.outbox), 1)

    @override_settings(ACCOUNT_RATE_LIMITS={"login": {"ip": (1, 60)}})
    def test_concurrent_requests_cannot_lose_rate_limit_increment(self):
        results = self.concurrent(
            lambda _: Client().post(reverse("accounts:login"), {"username": "missing@example.org"}).status_code,
            [1, 2],
        )
        self.assertCountEqual(results, [200, 429])
