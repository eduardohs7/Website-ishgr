from datetime import timedelta
from io import StringIO
from smtplib import SMTPException
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.core import mail
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import AccountActionToken, AccountEmail, ParticipantProfile, User
from accounts.services import consume_token, request_account_email

PASSWORD = "Synthetic-participant-password-47!"
NEW_PASSWORD = "Synthetic-replacement-password-92!"


class ParticipantFlowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.confirmed = User.objects.create_user(
            "confirmed@example.org", PASSWORD, full_name="Pessoa Confirmada",
            email_verified_at=timezone.now(),
        )
        cls.unconfirmed = User.objects.create_user(
            "unconfirmed@example.org", PASSWORD, full_name="Pessoa Pendente",
        )
        ParticipantProfile.objects.create(user=cls.confirmed, institution="Original")

    def queue(self, user, purpose):
        request_account_email(user.email, purpose)
        return AccountEmail.objects.get(token__user=user, token__purpose=purpose, status="pending")

    def send_and_read_token(self):
        output = StringIO()
        call_command("send_account_emails", stdout=output)
        message = mail.outbox[-1]
        link = next(line for line in message.body.splitlines() if line.startswith("http"))
        self.assertEqual(urlsplit(link).netloc, "127.0.0.1:8001")
        self.assertFalse(urlsplit(link).query)
        token = parse_qs(urlsplit(link).fragment)["token"][0]
        self.assertNotIn(token, output.getvalue())
        self.assertNotIn(message.to[0], output.getvalue())
        return token

    def test_signup_confirmation_login_profile_logout_end_to_end(self):
        response = self.client.post(reverse("accounts:signup"), {
            "email": " NEW+Event@EXAMPLE.ORG ", "full_name": "Nova Pessoa",
            "password1": PASSWORD, "password2": PASSWORD,
            "is_staff": "on", "is_superuser": "on", "email_verified_at": "2026-01-01",
        })
        self.assertRedirects(response, reverse("accounts:signup_done"))
        user = User.objects.get(email="new+event@example.org")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertIsNone(user.email_verified_at)
        self.assertTrue(ParticipantProfile.objects.filter(user=user).exists())
        self.assertEqual(AccountEmail.objects.filter(token__user=user).count(), 1)
        token = self.send_and_read_token()
        self.assertEqual(AccountEmail.objects.get(token__user=user).raw_token, "")
        self.client.get(reverse("accounts:confirm"))
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)  # Scanners visiting links cannot confirm accounts.
        self.assertRedirects(self.client.post(reverse("accounts:confirm"), {"token": token}), reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)
        login = self.client.post(reverse("accounts:login"), {"username": user.email.upper(), "password": PASSWORD})
        self.assertRedirects(login, reverse("accounts:dashboard"))
        self.assertEqual(self.client.session["_auth_user_id"], str(user.pk))
        self.assertRedirects(self.client.post(reverse("accounts:profile"), {
            "full_name": "Nome Atualizado", "institution": "UFPA", "country": "BR", "preferred_language": "en",
        }), reverse("accounts:profile"))
        user.refresh_from_db()
        self.assertEqual(user.full_name, "Nome Atualizado")
        self.assertEqual(str(user.profile.country), "BR")
        self.assertEqual(user.profile.preferred_language, "en")
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.assertIn("_auth_user_id", self.client.session)
        self.assertRedirects(self.client.post(reverse("accounts:logout")), reverse("accounts:login"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_duplicate_signup_has_same_response_and_never_overwrites_account(self):
        before = self.confirmed.password
        payload = {"full_name": "Outra Pessoa", "password1": PASSWORD, "password2": PASSWORD}
        fresh = self.client.post(reverse("accounts:signup"), {**payload, "email": "fresh@example.org"})
        duplicate = self.client.post(reverse("accounts:signup"), {**payload, "email": self.confirmed.email.upper()})
        self.assertEqual(fresh.status_code, duplicate.status_code)
        self.assertEqual(fresh["Location"], duplicate["Location"])
        self.confirmed.refresh_from_db()
        self.assertEqual(self.confirmed.password, before)
        self.assertEqual(self.confirmed.full_name, "Pessoa Confirmada")
        self.assertFalse(AccountEmail.objects.filter(token__user=self.confirmed).exists())

    def test_invalid_signup_does_not_create_users_or_deliveries(self):
        for password1, password2 in (("123", "123"), (PASSWORD, NEW_PASSWORD)):
            response = self.client.post(reverse("accounts:signup"), {
                "email": "invalid-new@example.org", "full_name": "Pessoa",
                "password1": password1, "password2": password2,
            })
            self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email="invalid-new@example.org").exists())
        self.assertEqual(AccountEmail.objects.count(), 0)

    def test_login_and_private_pages_require_confirmed_active_account(self):
        self.assertEqual(self.client.post(reverse("accounts:login"), {
            "username": self.unconfirmed.email, "password": PASSWORD,
        }).status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)
        for route in ("accounts:dashboard", "accounts:profile"):
            self.assertEqual(self.client.get(reverse(route)).status_code, 302)
        self.client.force_login(self.unconfirmed)
        self.assertRedirects(self.client.get(reverse("accounts:dashboard")), reverse("accounts:resend_confirmation"))
        self.client.logout()
        self.confirmed.is_active = False
        self.confirmed.save(update_fields=["is_active"])
        self.assertEqual(self.client.post(reverse("accounts:login"), {"username": self.confirmed.email, "password": PASSWORD}).status_code, 200)
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_login_redirects_only_to_safe_destinations(self):
        for target in ("https://evil.example/path", "//evil.example/path"):
            response = self.client.post(reverse("accounts:login"), {
                "username": self.confirmed.email, "password": PASSWORD, "next": target,
            })
            self.assertEqual(response["Location"], reverse("accounts:dashboard"))
            self.client.logout()
        response = self.client.post(reverse("accounts:login"), {
            "username": self.confirmed.email, "password": PASSWORD, "next": reverse("accounts:profile"),
        })
        self.assertEqual(response["Location"], reverse("accounts:profile"))

    def test_profile_updates_only_owner_and_whitelisted_fields(self):
        self.client.force_login(self.confirmed)
        response = self.client.post(reverse("accounts:profile"), {
            "full_name": "Nome Seguro", "institution": "Instituição", "country": "PT", "preferred_language": "es",
            "user": str(self.unconfirmed.pk), "id": str(self.unconfirmed.pk), "email": "attacker@example.org",
            "is_superuser": "on", "is_staff": "on", "password": "injected",
        })
        self.assertEqual(response.status_code, 302)
        self.confirmed.refresh_from_db()
        self.unconfirmed.refresh_from_db()
        self.assertEqual(self.confirmed.email, "confirmed@example.org")
        self.assertTrue(self.confirmed.check_password(PASSWORD))
        self.assertFalse(self.confirmed.is_superuser)
        self.assertFalse(self.confirmed.is_staff)
        self.assertEqual(self.unconfirmed.full_name, "Pessoa Pendente")
        self.assertFalse(ParticipantProfile.objects.filter(user=self.unconfirmed).exists())

    def test_invalid_country_or_language_does_not_update_profile(self):
        self.client.force_login(self.confirmed)
        for country, language in (("ZZ", "en"), ("BR", "admin")):
            response = self.client.post(reverse("accounts:profile"), {
                "full_name": "Alterado", "institution": "Alterada", "country": country, "preferred_language": language,
            })
            self.assertEqual(response.status_code, 200)
        self.confirmed.refresh_from_db()
        self.assertEqual(self.confirmed.full_name, "Pessoa Confirmada")
        self.assertEqual(self.confirmed.profile.institution, "Original")

    def test_user_content_is_escaped_in_private_templates(self):
        self.confirmed.full_name = '<script>alert("test")</script>'
        self.confirmed.save(update_fields=["full_name"])
        self.client.force_login(self.confirmed)
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertNotContains(response, '<script>alert("test")</script>')
        self.assertContains(response, "&lt;script&gt;")

    def test_all_mutating_account_forms_require_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.confirmed)
        for route in (
            "accounts:signup", "accounts:login", "accounts:logout", "accounts:resend_confirmation",
            "accounts:confirm", "accounts:request_reset", "accounts:reset", "accounts:profile",
        ):
            with self.subTest(route=route):
                self.assertEqual(client.post(reverse(route), {}).status_code, 403)

    def test_confirmation_and_reset_tokens_are_purpose_bound_and_single_use(self):
        confirmation = self.queue(self.unconfirmed, "confirm").raw_token
        reset = self.queue(self.confirmed, "reset").raw_token
        self.assertFalse(consume_token(reset, "confirm"))
        self.assertFalse(consume_token(confirmation, "reset", password=NEW_PASSWORD))
        self.assertTrue(consume_token(confirmation, "confirm"))
        self.assertFalse(consume_token(confirmation, "confirm"))
        self.assertTrue(consume_token(reset, "reset", password=NEW_PASSWORD))
        self.assertFalse(consume_token(reset, "reset", password=PASSWORD))

    def test_expired_tokens_cannot_confirm_or_reset(self):
        confirm = self.queue(self.unconfirmed, "confirm")
        reset = self.queue(self.confirmed, "reset")
        AccountActionToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertFalse(consume_token(confirm.raw_token, "confirm"))
        self.assertFalse(consume_token(reset.raw_token, "reset", password=NEW_PASSWORD))
        self.confirmed.refresh_from_db()
        self.unconfirmed.refresh_from_db()
        self.assertTrue(self.confirmed.check_password(PASSWORD))
        self.assertIsNone(self.unconfirmed.email_verified_at)

    def test_resend_revokes_old_token_and_cancels_its_delivery(self):
        first = self.queue(self.unconfirmed, "confirm")
        raw = first.raw_token
        second = self.queue(self.unconfirmed, "confirm")
        first.refresh_from_db()
        self.assertEqual(first.status, "cancelled")
        self.assertEqual(first.raw_token, "")
        self.assertFalse(consume_token(raw, "confirm"))
        self.assertTrue(consume_token(second.raw_token, "confirm"))

    def test_email_or_password_change_invalidates_existing_links(self):
        job = self.queue(self.confirmed, "reset")
        self.confirmed.set_password(NEW_PASSWORD)
        self.confirmed.save(update_fields=["password"])
        self.assertFalse(consume_token(job.raw_token, "reset", password=PASSWORD))
        job = self.queue(self.confirmed, "reset")
        self.confirmed.email = "changed@example.org"
        self.confirmed.save(update_fields=["email"])
        self.assertIsNone(self.confirmed.email_verified_at)
        self.assertFalse(consume_token(job.raw_token, "reset", password=PASSWORD))

    def test_recovery_response_does_not_reveal_accounts_or_accept_unconfirmed_users(self):
        locations = []
        for email in (self.confirmed.email.upper(), self.unconfirmed.email, "missing@example.org"):
            response = self.client.post(reverse("accounts:request_reset"), {"email": email})
            self.assertEqual(response.status_code, 302)
            locations.append(response["Location"])
        self.assertEqual(len(set(locations)), 1)
        self.assertEqual(AccountEmail.objects.count(), 1)
        self.assertEqual(AccountEmail.objects.get().token.user_id, self.confirmed.pk)

    def test_password_reset_flow_invalidates_existing_sessions(self):
        other_client = Client()
        other_client.force_login(self.confirmed)
        self.client.post(reverse("accounts:request_reset"), {"email": self.confirmed.email})
        raw = self.send_and_read_token()
        before = self.confirmed.password
        self.client.get(reverse("accounts:reset"))
        self.confirmed.refresh_from_db()
        self.assertEqual(self.confirmed.password, before)
        self.assertRedirects(self.client.post(reverse("accounts:reset"), {
            "token": raw, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD,
        }), reverse("accounts:reset_complete"))
        self.confirmed.refresh_from_db()
        self.assertTrue(self.confirmed.check_password(NEW_PASSWORD))
        self.assertFalse(self.confirmed.check_password(PASSWORD))
        self.assertEqual(other_client.get(reverse("accounts:dashboard")).status_code, 302)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(self.client.post(reverse("accounts:reset"), {
            "token": raw, "new_password1": PASSWORD, "new_password2": PASSWORD,
        }).status_code, 200)
        self.confirmed.refresh_from_db()
        self.assertTrue(self.confirmed.check_password(NEW_PASSWORD))

    def test_reset_rejects_weak_or_mismatched_password_without_consuming_token(self):
        job = self.queue(self.confirmed, "reset")
        for first, second in (("123", "123"), (NEW_PASSWORD, PASSWORD)):
            response = self.client.post(reverse("accounts:reset"), {
                "token": job.raw_token, "new_password1": first, "new_password2": second,
            })
            self.assertEqual(response.status_code, 200)
        job.token.refresh_from_db()
        self.assertIsNone(job.token.used_at)
        self.confirmed.refresh_from_db()
        self.assertTrue(self.confirmed.check_password(PASSWORD))

    def test_unknown_token_does_not_expose_user_data(self):
        response = self.client.post(reverse("accounts:reset"), {
            "token": "not-a-real-token", "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD,
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.confirmed.email)
        self.assertEqual(self.client.get("/conta/redefinir-senha/raw-token/").status_code, 404)

    def test_mail_error_does_not_roll_back_signup_and_is_retried(self):
        self.client.post(reverse("accounts:signup"), {
            "email": "mail-failure@example.org", "full_name": "Pessoa",
            "password1": PASSWORD, "password2": PASSWORD,
        })
        job = AccountEmail.objects.get(token__user__email="mail-failure@example.org")
        with patch("accounts.mail_delivery.EmailMessage.send", side_effect=SMTPException("private-error-detail")):
            output = StringIO()
            call_command("send_account_emails", stdout=output)
        job.refresh_from_db()
        self.assertEqual(job.status, "pending")
        self.assertEqual(job.attempts, 1)
        self.assertEqual(job.error_code, "delivery_failed")
        self.assertNotIn("private-error-detail", output.getvalue())
        self.assertTrue(User.objects.filter(email="mail-failure@example.org").exists())
        AccountEmail.objects.filter(pk=job.pk).update(available_at=timezone.now())
        self.send_and_read_token()
        job.refresh_from_db()
        self.assertEqual(job.status, "sent")
        self.assertEqual(job.attempts, 2)
        self.assertEqual(job.raw_token, "")

    def test_email_delivery_is_not_repeated_after_success_and_expired_jobs_are_cancelled(self):
        self.queue(self.confirmed, "reset")
        self.send_and_read_token()
        call_command("send_account_emails", stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        expired = self.queue(self.unconfirmed, "confirm")
        AccountActionToken.objects.filter(pk=expired.token_id).update(expires_at=timezone.now() - timedelta(seconds=1))
        call_command("send_account_emails", stdout=StringIO())
        expired.refresh_from_db()
        self.assertEqual(expired.status, "cancelled")
        self.assertEqual(expired.raw_token, "")
        self.assertEqual(len(mail.outbox), 1)
