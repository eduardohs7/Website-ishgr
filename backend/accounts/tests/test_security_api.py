import uuid
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import HASH_SESSION_KEY
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from django.contrib.sessions.models import Session
from django.db import DatabaseError
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
import pyotp

from accounts import mfa, security
from accounts.models import AccountActionToken, AccountEmail, AuthThrottleBucket, StaffMFA, User
from accounts.services import active_token, request_account_email
from accounts.tests.test_mfa import TEST_SECRET
from operations.models import AuditEntry
from registrations.tests import factories

PASSWORD = "Synthetic-registration-password-47!"
NEW_PASSWORD = "Synthetic-replacement-password-92!"
LIMITS = {**settings.ACCOUNT_RATE_LIMITS, "account_security": {"ip": (1000, 300), "identity": (1000, 300)}}


@override_settings(STAFF_MFA_REQUIRED=False, ACCOUNT_RATE_LIMITS=LIMITS)
class AccountSecurityTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = factories.user()
        cls.other = factories.user("other@example.org")

    def setUp(self):
        self.client.force_login(self.owner)
        self.second = Client()
        self.second.force_login(self.owner)
        self.foreign = Client()
        self.foreign.force_login(self.other)

    def payload(self, **extra):
        return {"current_password": PASSWORD, "new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD, **extra}

    def post(self, name="change_password", data=None, client=None, **extra):
        payload = data if data is not None else self.payload() if name == "change_password" else {"current_password": PASSWORD}
        return (client or self.client).post(reverse("auth_api:" + name), payload, content_type="application/json", **extra)

    def authenticated(self, client):
        return client.get(reverse("auth_api:session")).json()["data"]["authenticated"]

    def assert_error(self, response, status, code):
        self.assertEqual(response.status_code, status)
        self.assertEqual(response.json()["error"]["code"], code)
        self.assertIn("no-store", response["Cache-Control"])

    def reset_link(self):
        request_account_email(self.owner.email, "reset")
        return AccountEmail.objects.get(token__user=self.owner, status="pending").raw_token

    def enable_factor(self):
        User.objects.filter(pk=self.owner.pk).update(is_staff=True)
        return StaffMFA.objects.create(user=self.owner, enabled=True,
            secret_ciphertext=mfa.cipher().encrypt(TEST_SECRET.encode()).decode())

    def verify(self, now):
        response = self.client.post(reverse("staff_mfa:verify"), {"code": pyotp.TOTP(TEST_SECRET).at(now)}, content_type="application/json")
        self.assertEqual(response.status_code, 200)

    def test_authenticated_change_keeps_only_current_session_and_new_login_works(self):
        old_key = self.client.session.session_key
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data"]["code"], "password_changed")
        self.assertTrue(response.json()["data"]["authenticated"])
        self.assertEqual(len(response.json()["data"]["csrf_token"]), 64)
        self.assertNotEqual(self.client.session.session_key, old_key)
        self.assertFalse(Session.objects.filter(session_key=old_key).exists())
        self.assertTrue(self.authenticated(self.client))
        self.assertFalse(self.authenticated(self.second))
        self.assertTrue(self.authenticated(self.foreign))
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(NEW_PASSWORD))
        self.assertFalse(self.owner.check_password(PASSWORD))
        self.assertIsNotNone(self.owner.session_version)
        login = Client()
        self.assertEqual(login.post(reverse("auth_api:login"), {"email": self.owner.email, "password": NEW_PASSWORD}, content_type="application/json").status_code, 200)
        self.assertTrue(self.authenticated(login))
        self.assertEqual(login.post(reverse("auth_api:login"), {"email": self.owner.email, "password": PASSWORD}, content_type="application/json").status_code, 401)

    def test_logout_all_includes_current_session_without_changing_password(self):
        original = self.owner.password
        response = self.post("logout_all")
        self.assertEqual(response.status_code, 200)

        self.assertEqual(response.json()["data"]["code"], "all_sessions_logged_out")
        self.assertFalse(response.json()["data"]["authenticated"])
        self.assertFalse(self.authenticated(self.client))
        self.assertFalse(self.authenticated(self.second))
        self.assertTrue(self.authenticated(self.foreign))
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.password, original)
        self.assertIsNotNone(self.owner.session_version)
        response = Client().post(reverse("auth_api:login"), {"email": self.owner.email, "password": PASSWORD}, content_type="application/json")
        self.assertEqual(response.status_code, 200)

    def test_ordinary_save_of_stale_user_cannot_undo_global_revocation(self):
        stale = User.objects.get(pk=self.owner.pk)
        self.assertIsNone(stale.session_version)
        self.assertEqual(self.post("logout_all").status_code, 200)
        self.owner.refresh_from_db()
        version = self.owner.session_version
        stale.full_name = "Nome atualizado por outra operação"
        stale.save()
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.session_version, version)
        self.assertEqual(self.owner.full_name, stale.full_name)
        self.assertFalse(self.authenticated(self.second))

    def test_anonymous_inactive_and_stale_sessions_cannot_change_security(self):
        for name in ("change_password", "logout_all"):
            self.assert_error(self.post(name, client=Client()), 401, "authentication_required")
        User.objects.filter(pk=self.owner.pk).update(is_active=False)
        self.assert_error(self.post(), 401, "authentication_required")
        self.assertFalse(AuditEntry.objects.exists())

    def test_ordinary_save_of_stale_user_cannot_restore_previous_password(self):
        stale = User.objects.get(pk=self.owner.pk)
        self.assertEqual(self.post().status_code, 200)
        stale.full_name = "Atualização posterior do nome"
        stale.save()
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(NEW_PASSWORD))
        self.assertFalse(self.owner.check_password(PASSWORD))
        self.assertIsNotNone(self.owner.session_version)
        self.assertTrue(self.authenticated(self.client))

    def test_wrong_current_password_never_changes_password_session_or_tokens(self):
        token = self.reset_link()
        old_key = self.client.session.session_key
        for name, payload in (("change_password", self.payload(current_password="invalid")),
                              ("logout_all", {"current_password": "invalid"})):
            self.assert_error(self.post(name, payload), 401, "invalid_credentials")
        self.owner.refresh_from_db()
        self.assertIsNone(self.owner.session_version)
        self.assertTrue(self.owner.check_password(PASSWORD))
        self.assertEqual(self.client.session.session_key, old_key)
        self.assertTrue(self.authenticated(self.second))
        self.assertIsNotNone(active_token(token, "reset"))
        self.assertFalse(AuditEntry.objects.exists())

    def test_password_policy_mismatch_and_same_password_are_rejected(self):
        for first, second in (("short", "short"), ("1234567890123", "1234567890123"), (NEW_PASSWORD, "different")):
            response = self.post(data=self.payload(new_password1=first, new_password2=second))
            self.assert_error(response, 422, "validation_error")
            self.assertIn("fields", response.json()["error"])
        self.assert_error(self.post(data=self.payload(new_password1=PASSWORD, new_password2=PASSWORD)), 409, "password_unchanged")
        self.owner.refresh_from_db()
        self.assertIsNone(self.owner.session_version)
        self.assertTrue(self.owner.check_password(PASSWORD))

    def test_payload_identity_and_security_flags_cannot_target_another_account(self):
        for key in ("user_id", "email", "session_version", "is_staff", "mfa_verified", "keep_other_sessions"):
            self.assert_error(self.post(data=self.payload(**{key: str(self.other.pk)})), 400, "unknown_fields")
        self.assert_error(self.post("logout_all", {"current_password": PASSWORD, "user_id": str(self.other.pk)}), 400, "unknown_fields")
        self.assertTrue(self.authenticated(self.foreign))

    def test_strict_methods_queries_types_and_body_limits(self):
        for name in ("change_password", "logout_all"):
            self.assert_error(self.client.get(reverse("auth_api:" + name)), 405, "method_not_allowed")
        response = self.client.post(reverse("auth_api:logout_all") + "?user_id=other", {"current_password": PASSWORD}, content_type="application/json")
        self.assert_error(response, 400, "unknown_fields")
        self.assert_error(self.post(data=self.payload(current_password=True)), 400, "invalid_payload")
        self.assert_error(self.post(data=self.payload(new_password1="x" * 33000)), 413, "payload_too_large")
        self.assert_error(self.client.post(reverse("auth_api:logout_all"), "x", content_type="text/plain"), 415, "unsupported_media_type")
        response = self.client.post(reverse("auth_api:logout_all"), '{"current_password":"a","current_password":"b"}', content_type="application/json")
        self.assert_error(response, 400, "invalid_payload")
        self.assert_error(self.post("logout_all", {}), 422, "validation_error")

    def test_old_action_tokens_and_unsent_codes_are_revoked_by_change(self):
        token = self.reset_link()
        self.assertIsNotNone(active_token(token, "reset"))
        self.assertEqual(self.post().status_code, 200)
        self.assertIsNone(active_token(token, "reset"))
        action = AccountActionToken.objects.get(user=self.owner)
        self.assertIsNotNone(action.revoked_at)
        delivery = AccountEmail.objects.get(token=action)
        self.assertEqual(delivery.status, "cancelled")
        self.assertEqual(delivery.raw_token, "")
        self.assertFalse(AccountEmail.objects.filter(status="pending").exists())

    def test_logout_revokes_reset_links_and_processing_email_without_touching_other_account(self):
        token = self.reset_link()
        AccountEmail.objects.filter(token__user=self.owner).update(status="processing", lease_until=timezone.now() + timedelta(minutes=1))
        request_account_email(self.other.email, "reset")
        self.assertEqual(self.post("logout_all").status_code, 200)
        self.assertIsNone(active_token(token, "reset"))
        own = AccountEmail.objects.get(token__user=self.owner)
        self.assertEqual(own.status, "cancelled")
        self.assertEqual(own.raw_token, "")
        self.assertIsNone(own.lease_until)
        self.assertEqual(AccountEmail.objects.get(token__user=self.other).status, "pending")

    def test_new_reset_link_after_epoch_rotation_still_updates_password_and_revokes_session(self):
        self.assertEqual(self.post().status_code, 200)
        raw = self.reset_link()
        self.assertIsNotNone(active_token(raw, "reset"))
        replacement = "Synthetic-reset-after-epoch-password-56!"
        response = self.client.post(reverse("auth_api:reset"), {
            "token": raw, "password1": replacement, "password2": replacement,
        }, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.authenticated(self.client))
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(replacement))

    def test_security_audit_has_no_credentials_tokens_or_session_identifier(self):
        old_key = self.client.session.session_key
        token = self.reset_link()
        self.assertEqual(self.post().status_code, 200)
        entry = AuditEntry.objects.get(action="security.password_changed")
        self.assertEqual(entry.actor_id, self.owner.pk)
        self.assertEqual(entry.target_type, "accounts.user")
        self.assertEqual(entry.target_id, str(self.owner.pk))
        self.assertEqual(entry.details, {})
        serialized = str(list(AuditEntry.objects.values()))
        for value in (PASSWORD, NEW_PASSWORD, token, old_key):
            self.assertNotIn(value, serialized)

    def test_audit_failure_rolls_back_password_epoch_tokens_and_preserves_sessions(self):
        token = self.reset_link()
        for name in ("change_password", "logout_all"):
            with patch("accounts.security.audit", side_effect=DatabaseError("synthetic-private-value")):
                response = self.post(name)
            self.assert_error(response, 503, "operation_unavailable")
            self.assertNotContains(response, "synthetic-private-value", status_code=503)
            self.owner.refresh_from_db()
            self.assertIsNone(self.owner.session_version)
            self.assertTrue(self.owner.check_password(PASSWORD))
            self.assertIsNotNone(active_token(token, "reset"))
            self.assertTrue(self.authenticated(self.client))
            self.assertTrue(self.authenticated(self.second))
        self.assertFalse(AuditEntry.objects.exists())

    def test_session_save_failure_rolls_back_password_epoch_and_audit(self):
        with patch("django.contrib.sessions.backends.db.SessionStore.save", side_effect=DatabaseError("synthetic-private-value")):
            response = self.post()
        self.assert_error(response, 503, "operation_unavailable")
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.check_password(PASSWORD))
        self.assertIsNone(self.owner.session_version)
        self.assertFalse(AuditEntry.objects.exists())
        self.assertTrue(self.authenticated(self.client))
        self.assertTrue(self.authenticated(self.second))

    def test_session_delete_failure_rolls_back_global_revocation(self):
        with patch("django.contrib.sessions.backends.db.SessionStore.delete", side_effect=DatabaseError("synthetic-private-value")):
            response = self.post("logout_all")
        self.assert_error(response, 503, "operation_unavailable")
        self.owner.refresh_from_db()
        self.assertIsNone(self.owner.session_version)
        self.assertFalse(AuditEntry.objects.exists())
        self.assertTrue(self.authenticated(self.second))

    def test_csrf_is_required_and_tokens_rotate_after_change(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assert_error(self.post(client=client), 403, "csrf_failed")
        token = client.get(reverse("auth_api:session")).json()["data"]["csrf_token"]
        self.assert_error(self.post(client=client, HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="https://foreign.example"), 403, "csrf_failed")
        response = self.post(client=client, HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        next_token = response.json()["data"]["csrf_token"]
        self.assertNotEqual(token, next_token)
        self.assert_error(self.post("logout_all", {"current_password": NEW_PASSWORD}, client=client, HTTP_X_CSRFTOKEN=token), 403, "csrf_failed")
        self.assertEqual(self.post("logout_all", {"current_password": NEW_PASSWORD}, client=client, HTTP_X_CSRFTOKEN=next_token).status_code, 200)

    def test_remembered_or_browser_session_expiry_is_preserved_on_change(self):
        session = self.client.session
        session.set_expiry(0)
        session.save()
        self.assertEqual(self.post().status_code, 200)
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_migration_default_preserves_legacy_hash_tokens_and_sessions(self):
        self.assertIsNone(self.owner.session_version)
        self.assertEqual(self.owner.get_session_auth_hash(), AbstractBaseUser._get_session_auth_hash(self.owner))
        self.assertTrue(self.authenticated(self.client))
        self.assertIsNotNone(active_token(self.reset_link(), "reset"))

    def test_secret_key_fallback_supports_rotated_session_epoch(self):
        self.assertEqual(self.post().status_code, 200)
        old_secret = settings.SECRET_KEY
        with override_settings(SECRET_KEY="Synthetic-new-secret-for-fallback-tests-12345678901234567890", SECRET_KEY_FALLBACKS=[old_secret]):
            self.assertTrue(self.authenticated(self.client))

    def test_legacy_password_hash_is_not_upgraded_by_failed_operation(self):
        encoded = PBKDF2PasswordHasher().encode(PASSWORD, "synthetic-salt", iterations=1)
        User.objects.filter(pk=self.owner.pk).update(password=encoded)
        self.owner.refresh_from_db()
        self.client.force_login(self.owner)
        self.assert_error(self.post(data=self.payload(new_password1="short", new_password2="short")), 422, "validation_error")
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.password, encoded)
        self.assertTrue(self.authenticated(self.client))

    def test_unusable_password_cannot_authorize_security_change(self):
        self.owner.set_unusable_password()
        self.owner.save(update_fields=["password"])
        self.client.force_login(self.owner)
        self.assert_error(self.post(), 401, "invalid_credentials")

    def test_preloaded_revoked_session_is_rejected_under_account_lock(self):
        request = RequestFactory().post("/api/v1/auth/change-password/")
        request.user = self.owner
        request.session = self.client.session
        User.objects.filter(pk=self.owner.pk).update(session_version=uuid.uuid4())
        for operation, data in ((security.change_password, self.payload()), (security.logout_all, {"current_password": PASSWORD})):
            with self.assertRaises(security.SecurityError) as caught:
                operation(request, **data)
            self.assertEqual(caught.exception.code, "authentication_required")
        self.assertFalse(AuditEntry.objects.exists())

    def test_preloaded_revoked_session_cannot_obtain_new_mfa_proof(self):
        factor = self.enable_factor()
        request = RequestFactory().post("/api/v1/auth/mfa/verify/")
        request.user = self.owner
        request.session = self.client.session
        User.objects.filter(pk=self.owner.pk).update(session_version=uuid.uuid4())
        with self.assertRaises(mfa.MFAError) as caught:
            mfa.verify(request, code=pyotp.TOTP(TEST_SECRET).now(), method="totp")
        self.assertEqual(caught.exception.code, "authentication_required")
        factor.refresh_from_db()
        self.assertEqual(factor.last_step, -1)
        self.assertFalse(AuditEntry.objects.exists())

    @override_settings(STAFF_MFA_REQUIRED=True)
    def test_staff_must_enroll_mfa_for_password_change_but_can_reduce_access_without_it(self):
        User.objects.filter(pk=self.owner.pk).update(is_staff=True)
        self.assert_error(self.post(), 403, "mfa_enrollment_required")
        self.assertEqual(self.post("logout_all").status_code, 200)

    def test_staff_password_change_requires_recent_mfa_then_preserves_factor_and_recovery(self):
        factor = self.enable_factor()
        codes = mfa.new_recovery_codes(factor)
        now = timezone.now()
        with patch("accounts.mfa.timezone.now", return_value=now):
            self.assert_error(self.post(), 403, "mfa_required")
            self.verify(now)
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["data"]["mfa_verification_required"])
        factor.refresh_from_db()
        self.assertTrue(factor.enabled)
        self.assertEqual(factor.recovery_codes.count(), len(codes))
        self.assertEqual(factor.recovery_codes.filter(used_at__isnull=False).count(), 0)
        self.assertNotIn(mfa.PROOF_KEY, self.client.session)
        self.assertTrue(self.authenticated(self.client))
        self.assertFalse(self.authenticated(self.second))
        self.assert_error(self.client.get(reverse("management_api:session")), 403, "mfa_required")
        # Last consumed TOTP is not reset by password change.
        with patch("accounts.mfa.timezone.now", return_value=now):
            response = self.client.post(reverse("staff_mfa:verify"), {"code": pyotp.TOTP(TEST_SECRET).at(now)}, content_type="application/json")
            self.assert_error(response, 401, "invalid_mfa_code")

    def test_one_hour_mfa_proof_is_not_sufficient_for_sensitive_password_change(self):
        self.enable_factor()
        now = timezone.now()
        with patch("accounts.mfa.timezone.now", return_value=now):
            self.verify(now)
        with patch("accounts.mfa.timezone.now", return_value=now + timedelta(seconds=settings.STAFF_MFA_RECENT_AGE + 1)):
            self.assert_error(self.post(), 403, "mfa_required")

    def test_logout_does_not_disable_mfa_reset_consumed_codes_or_keep_pending_enrollment(self):
        factor = self.enable_factor()
        mfa.new_recovery_codes(factor)
        used = factor.recovery_codes.first()
        used.used_at = timezone.now()
        used.save(update_fields=["used_at"])
        factor.pending_ciphertext = "synthetic-pending-ciphertext"
        factor.pending_auth_hash = self.owner.get_session_auth_hash()
        factor.pending_challenge_hash = "synthetic-hash"
        factor.pending_expires_at = timezone.now() + timedelta(minutes=10)
        factor.last_step = 15
        factor.save()
        self.assertEqual(self.post("logout_all").status_code, 200)
        factor.refresh_from_db()
        self.assertTrue(factor.enabled)
        self.assertEqual(factor.last_step, 15)
        self.assertEqual(factor.pending_ciphertext, "")
        self.assertIsNone(factor.pending_expires_at)
        self.assertEqual(factor.recovery_codes.filter(used_at__isnull=False).count(), 1)

    def test_technical_staff_without_verified_email_can_secure_its_own_account(self):
        User.objects.filter(pk=self.owner.pk).update(is_staff=True, email_verified_at=None)
        self.assertEqual(self.post().status_code, 200)
        self.assertEqual(self.client.get(reverse("management_api:session")).status_code, 200)


@override_settings(STAFF_MFA_REQUIRED=False, ACCOUNT_RATE_LIMITS={**LIMITS, "account_security": {"ip": (20, 60), "identity": (2, 60)}})
class SecurityRateTests(TestCase):
    def setUp(self):
        self.owner = factories.user()
        self.clients = [Client(), Client(), Client()]
        for client in self.clients:
            client.force_login(self.owner)

    def test_limit_is_shared_between_routes_sessions_and_ip_addresses(self):
        for index, (name, client) in enumerate(zip(("logout_all", "change_password", "logout_all"), self.clients)):
            payload = {"current_password": "incorrect"}
            if name == "change_password":
                payload.update(new_password1=NEW_PASSWORD, new_password2=NEW_PASSWORD)
            response = client.post(reverse("auth_api:" + name), payload, content_type="application/json", REMOTE_ADDR=f"203.0.113.{index+1}")
            self.assertEqual(response.status_code, 429 if index == 2 else 401)
        self.assertGreater(int(response["Retry-After"]), 0)
        self.assertIn("no-store", response["Cache-Control"])
        for key in AuthThrottleBucket.objects.values_list("key", flat=True):
            self.assertEqual(len(key), 64)
            self.assertNotIn(str(self.owner.pk), key)
            self.assertNotIn("203.0.113", key)

    def test_audit_rollback_does_not_reset_attempt_counters(self):
        for _ in range(2):
            with patch("accounts.security.audit", side_effect=DatabaseError("synthetic")):
                response = self.clients[0].post(reverse("auth_api:logout_all"), {"current_password": PASSWORD}, content_type="application/json")
            self.assertEqual(response.status_code, 503)
        response = self.clients[0].post(reverse("auth_api:logout_all"), {"current_password": PASSWORD}, content_type="application/json")
        self.assertEqual(response.status_code, 429)

    def test_limiter_failure_does_not_allow_unlimited_password_attempts(self):
        with patch("accounts.middleware.AuthThrottleBucket.objects.get_or_create", side_effect=DatabaseError("synthetic-private-value")):
            response = self.clients[0].post(reverse("auth_api:logout_all"), {"current_password": PASSWORD}, content_type="application/json")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "operation_unavailable")
        self.assertFalse(AuditEntry.objects.exists())
