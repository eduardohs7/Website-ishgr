import io
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.hashers import PBKDF2PasswordHasher
from cryptography.fernet import Fernet
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import DatabaseError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
import pyotp

from accounts import mfa
from accounts.models import AuthThrottleBucket, StaffMFA, StaffMFARecoveryCode, User
from operations.models import AuditEntry
from registrations.tests import factories

PASSWORD = "Synthetic-registration-password-47!"
TEST_SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # Public RFC 6238 vector.
LIMITS = {**settings.ACCOUNT_RATE_LIMITS, "mfa_code": {"ip": (1000, 300), "identity": (1000, 300)},
          "mfa_sensitive": {"ip": (1000, 300), "identity": (1000, 300)}}


@override_settings(STAFF_MFA_REQUIRED=False, ACCOUNT_RATE_LIMITS=LIMITS)
class StaffMFATests(TestCase):
    def setUp(self):
        self.actor = factories.user("operator@example.org", is_staff=True, is_superuser=True)
        self.client.force_login(self.actor)
        self.now = timezone.now()
        clock = patch("accounts.mfa.timezone.now", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def post(self, name, data, client=None, **extra):
        return (client or self.client).post(reverse("staff_mfa:" + name), data, content_type="application/json", **extra)

    def factor(self):
        return StaffMFA.objects.create(user=self.actor, enabled=True,
            secret_ciphertext=mfa.cipher().encrypt(TEST_SECRET.encode()).decode())

    def code(self):
        return pyotp.TOTP(TEST_SECRET).at(self.now)

    def bad_code(self):
        step = int(timezone.now().timestamp()) // 30
        valid = {pyotp.TOTP(TEST_SECRET).at(index * 30) for index in (step - 1, step, step + 1)}
        return next(f"{index:06d}" for index in range(4) if f"{index:06d}" not in valid)

    def authenticate(self):
        response = self.post("verify", {"code": self.code()})
        self.assertEqual(response.status_code, 200)
        return response

    def test_only_active_staff_can_access_mfa_endpoints(self):
        anonymous = Client()
        self.assertEqual(anonymous.get(reverse("staff_mfa:status")).status_code, 401)
        self.assertEqual(self.post("enroll", {"password": PASSWORD}, client=anonymous).status_code, 401)
        self.actor.is_staff = False
        self.actor.save(update_fields=["is_staff"])
        self.assertEqual(self.client.get(reverse("staff_mfa:status")).status_code, 403)
        self.assertEqual(self.post("enroll", {"password": PASSWORD}).status_code, 403)
        self.actor.is_active = False
        self.actor.save(update_fields=["is_active"])
        self.assertEqual(self.client.get(reverse("staff_mfa:status")).status_code, 401)

    def test_status_contains_no_secret_and_does_not_create_factor(self):
        response = self.client.get(reverse("staff_mfa:status"))
        data = response.json()["data"]
        self.assertFalse(data["enabled"] or data["verified"] or data["pending_enrollment"])
        self.assertEqual(data["recovery_codes_remaining"], 0)
        self.assertFalse(StaffMFA.objects.exists())
        self.assertNotIn("secret", data)
        self.assertNotIn("provisioning_uri", data)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(len(data["csrf_token"]), 64)

    def test_begin_requires_current_password_and_stores_only_encrypted_pending_secret(self):
        self.assertEqual(self.post("enroll", {"password": "wrong"}).status_code, 401)
        self.assertFalse(StaffMFA.objects.exists())
        response = self.post("enroll", {"password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        data = response.json()["data"]
        factor = StaffMFA.objects.get(user=self.actor)
        self.assertFalse(factor.enabled)
        self.assertTrue(data["secret"] not in factor.pending_ciphertext)
        self.assertTrue(mfa.decrypt(factor.pending_ciphertext) == data["secret"])
        self.assertTrue(data["provisioning_uri"].startswith("otpauth://totp/"))
        self.assertEqual(AuditEntry.objects.get(action="security.mfa_enrollment_started").details, {})
        self.assertIn("no-store", response["Cache-Control"])

    def test_confirm_enables_factor_rotates_session_and_issues_ten_hashed_codes_once(self):
        data = self.post("enroll", {"password": PASSWORD}).json()["data"]
        old_session = self.client.session.session_key
        response = self.post("confirm", {"code": pyotp.TOTP(data["secret"]).at(self.now)})
        self.assertEqual(response.status_code, 200)
        codes = response.json()["data"]["recovery_codes"]
        factor = StaffMFA.objects.get(user=self.actor)
        self.assertTrue(factor.enabled)
        self.assertEqual(len(codes), 10)
        self.assertEqual(factor.recovery_codes.count(), 10)
        digests = set(factor.recovery_codes.values_list("digest", flat=True))
        self.assertTrue(all(code.replace("-", "") not in digests for code in codes))
        self.assertFalse(factor.pending_ciphertext or factor.pending_auth_hash or factor.pending_challenge_hash)
        self.assertTrue(old_session != self.client.session.session_key)
        self.assertTrue(self.client.get(reverse("staff_mfa:status")).json()["data"]["verified"])
        self.assertEqual(self.post("confirm", {"code": "123456"}).status_code, 409)
        self.assertEqual(AuditEntry.objects.filter(action="security.mfa_enabled").count(), 1)

    def test_enrollment_is_bound_to_the_browser_that_started_it(self):
        data = self.post("enroll", {"password": PASSWORD}).json()["data"]
        other = Client()
        other.force_login(self.actor)
        response = self.post("confirm", {"code": pyotp.TOTP(data["secret"]).at(self.now)}, client=other)
        self.assertEqual(response.status_code, 409)
        self.assertFalse(StaffMFA.objects.get(user=self.actor).enabled)

    def test_expired_pending_enrollment_cannot_be_confirmed(self):
        data = self.post("enroll", {"password": PASSWORD}).json()["data"]
        StaffMFA.objects.filter(user=self.actor).update(pending_expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.post("confirm", {"code": pyotp.TOTP(data["secret"]).at(self.now)}).status_code, 409)

    def test_new_begin_invalidates_prior_enrollment_even_if_old_code_was_valid(self):
        first = self.post("enroll", {"password": PASSWORD}).json()["data"]
        second = self.post("enroll", {"password": PASSWORD}).json()["data"]
        factor = StaffMFA.objects.get(user=self.actor)
        self.assertTrue(mfa.decrypt(factor.pending_ciphertext) == second["secret"])
        self.assertTrue(first["secret"] != second["secret"])

    def test_password_change_invalidates_pending_enrollment(self):
        data = self.post("enroll", {"password": PASSWORD}).json()["data"]
        self.actor.set_password("Synthetic-new-password-47!")
        self.actor.save(update_fields=["password"])
        self.client.force_login(self.actor)
        self.assertEqual(self.post("confirm", {"code": pyotp.TOTP(data["secret"]).at(self.now)}).status_code, 409)

    def test_invalid_confirmation_does_not_enable_or_issue_recovery_codes(self):
        data = self.post("enroll", {"password": PASSWORD}).json()["data"]
        step = int(timezone.now().timestamp()) // 30
        valid = {pyotp.TOTP(data["secret"]).at(index * 30) for index in (step - 1, step, step + 1)}
        bad = next(f"{index:06d}" for index in range(4) if f"{index:06d}" not in valid)
        self.assertEqual(self.post("confirm", {"code": bad}).status_code, 401)
        self.assertFalse(StaffMFA.objects.get(user=self.actor).enabled)
        self.assertFalse(StaffMFARecoveryCode.objects.exists())

    def test_wrong_totp_does_not_grant_proof_or_audit_success(self):
        self.factor()
        self.assertEqual(self.post("verify", {"code": self.bad_code()}).status_code, 401)
        self.assertNotIn(mfa.PROOF_KEY, self.client.session)
        self.assertFalse(AuditEntry.objects.filter(action="security.mfa_verified").exists())

    def test_correct_totp_grants_only_current_session_and_cannot_be_replayed(self):
        self.factor()
        old_session = self.client.session.session_key
        self.authenticate()
        self.assertTrue(old_session != self.client.session.session_key)
        other = Client()
        other.force_login(self.actor)
        self.assertFalse(other.get(reverse("staff_mfa:status")).json()["data"]["verified"])
        self.assertEqual(self.post("verify", {"code": self.code()}, client=other).status_code, 401)
        self.assertEqual(AuditEntry.objects.filter(action="security.mfa_verified").count(), 1)

    def test_recovery_code_is_single_use_and_bound_to_its_user(self):
        factor = self.factor()
        code = mfa.new_recovery_codes(factor)[0]
        response = self.post("verify", {"code": code, "method": "recovery"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(factor.recovery_codes.filter(used_at__isnull=False).count(), 1)
        self.assertEqual(self.post("verify", {"code": code, "method": "recovery"}).status_code, 401)
        other_user = factories.user("other-staff@example.org", is_staff=True)
        StaffMFA.objects.create(user=other_user, enabled=True, secret_ciphertext=factor.secret_ciphertext)
        other = Client()
        other.force_login(other_user)
        unused = mfa.new_recovery_codes(factor)[0]
        self.assertEqual(self.post("verify", {"code": unused, "method": "recovery"}, client=other).status_code, 401)

    def test_enabled_factor_blocks_admin_legacy_management_api_and_export(self):
        self.factor()
        for path in ("/gestao/", "/gestao/accounts/user/", "/gestao/inscricoes/", "/gestao/indicadores/",
                     "/gestao/exportacoes/", "/api/v1/management/session/", "/api/v1/management/registrations/"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["error"]["code"], "mfa_required")
            self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(self.client.post("/api/v1/management/registrations/export/", {}, content_type="application/json").status_code, 403)
        self.authenticate()
        self.assertEqual(self.client.get("/gestao/").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/management/session/").status_code, 200)

    @override_settings(STAFF_MFA_REQUIRED=True)
    def test_mandatory_mode_blocks_unenrolled_staff_but_allows_status_and_enrollment(self):
        for path in ("/gestao/", "/api/v1/management/session/"):
            response = self.client.get(path)
            self.assertEqual(response.json()["error"]["code"], "mfa_enrollment_required")
        self.assertEqual(self.client.get(reverse("staff_mfa:status")).status_code, 200)
        self.assertEqual(self.post("enroll", {"password": PASSWORD}).status_code, 200)

    def test_optional_mode_preserves_unenrolled_staff_and_participant_flow(self):
        self.assertEqual(self.client.get("/gestao/").status_code, 200)
        self.factor()
        self.assertEqual(self.client.get("/api/v1/participant/profile/").status_code, 200)
        self.assertEqual(self.client.get("/gestao/login/").status_code, 302)
        self.assertEqual(self.client.get("/health/live/").status_code, 200)

    def test_real_password_login_only_grants_first_factor_until_totp_verification(self):
        self.factor()
        client = Client()
        response = client.post(reverse("auth_api:login"), {"email": self.actor.email, "password": PASSWORD}, content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(client.get("/api/v1/management/session/").json()["error"]["code"], "mfa_required")
        self.assertEqual(self.post("verify", {"code": self.code()}, client=client).status_code, 200)
        self.assertEqual(client.get("/api/v1/management/session/").status_code, 200)

    def test_script_prefix_cannot_bypass_management_gate(self):
        self.factor()
        response = self.client.get("/gestao/", SCRIPT_NAME="/event")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "mfa_required")

    def test_csrf_rotates_after_confirm_and_old_token_cannot_manage_factor(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        old_token = client.get(reverse("staff_mfa:status")).json()["data"]["csrf_token"]
        data = self.post("enroll", {"password": PASSWORD}, client=client, HTTP_X_CSRFTOKEN=old_token).json()["data"]
        confirmed = self.post("confirm", {"code": pyotp.TOTP(data["secret"]).at(self.now)}, client=client, HTTP_X_CSRFTOKEN=old_token)
        self.assertEqual(confirmed.status_code, 200)
        new_token = confirmed.json()["data"]["csrf_token"]
        self.assertEqual(self.post("recovery_codes", {"password": PASSWORD}, client=client, HTTP_X_CSRFTOKEN=old_token).status_code, 403)
        self.assertEqual(self.post("recovery_codes", {"password": PASSWORD}, client=client, HTTP_X_CSRFTOKEN=new_token).status_code, 200)

    def test_proof_expiry_requires_new_challenge_and_does_not_extend_on_reads(self):
        self.factor()
        self.authenticate()
        session = self.client.session
        original = session[mfa.PROOF_KEY]["at"]
        self.client.get(reverse("staff_mfa:status"))
        self.assertEqual(self.client.session[mfa.PROOF_KEY]["at"], original)
        session[mfa.PROOF_KEY]["at"] = timezone.now().timestamp() - settings.STAFF_MFA_MAX_AGE - 1
        session.save()
        self.assertEqual(self.client.get("/gestao/").json()["error"]["code"], "mfa_required")

    def test_proof_is_bound_to_user_factor_version_and_password_hash(self):
        factor = self.factor()
        self.authenticate()
        for field, bad in (("user", "another-user"), ("version", "bad"), ("auth", "bad"), ("at", "bad")):
            session = self.client.session
            good = session[mfa.PROOF_KEY][field]
            session[mfa.PROOF_KEY][field] = bad
            session.save()
            self.assertFalse(self.client.get(reverse("staff_mfa:status")).json()["data"]["verified"])
            session[mfa.PROOF_KEY][field] = good
            session.save()
        factor.version = uuid.uuid4()
        factor.save(update_fields=["version"])
        self.assertFalse(self.client.get(reverse("staff_mfa:status")).json()["data"]["verified"])

    def test_password_reset_does_not_remove_second_factor(self):
        self.factor()
        self.authenticate()
        self.actor.set_password("Synthetic-new-password-47!")
        self.actor.save(update_fields=["password"])
        self.client.force_login(self.actor)
        self.assertTrue(StaffMFA.objects.get(user=self.actor).enabled)
        self.assertEqual(self.client.get("/gestao/").json()["error"]["code"], "mfa_required")

    def test_reenrollment_requires_recent_proof_and_preserves_old_device_until_confirm(self):
        factor = self.factor()
        self.assertEqual(self.post("enroll", {"password": PASSWORD}).status_code, 403)
        self.authenticate()
        self.assertEqual(self.post("enroll", {"password": PASSWORD}).status_code, 200)
        factor.refresh_from_db()
        self.assertTrue(factor.enabled and mfa.decrypt(factor.secret_ciphertext) == TEST_SECRET)
        session = self.client.session
        session[mfa.PROOF_KEY]["at"] -= settings.STAFF_MFA_RECENT_AGE + 1
        session.save()
        self.assertEqual(self.post("enroll", {"password": PASSWORD}).status_code, 403)

    def test_regenerating_recovery_codes_requires_password_and_recent_proof(self):
        factor = self.factor()
        old_codes = mfa.new_recovery_codes(factor)
        self.assertEqual(self.post("recovery_codes", {"password": PASSWORD}).status_code, 403)
        self.authenticate()
        self.assertEqual(self.post("recovery_codes", {"password": "wrong"}).status_code, 401)
        response = self.post("recovery_codes", {"password": PASSWORD})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["data"]["recovery_codes"]), 10)
        self.assertEqual(self.post("verify", {"code": old_codes[0], "method": "recovery"}).status_code, 401)
        self.assertEqual(AuditEntry.objects.filter(action="security.mfa_recovery_rotated").count(), 1)

    @override_settings(STAFF_MFA_REQUIRED=True)
    def test_required_mode_never_allows_disabling_factor(self):
        self.factor()
        self.authenticate()
        response = self.post("disable", {"password": PASSWORD})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "mfa_mandatory")
        self.assertTrue(StaffMFA.objects.get(user=self.actor).enabled)

    def test_optional_disable_requires_two_factors_and_deletes_secrets_and_codes(self):
        factor = self.factor()
        mfa.new_recovery_codes(factor)
        self.assertEqual(self.post("disable", {"password": PASSWORD}).status_code, 403)
        self.authenticate()
        self.assertEqual(self.post("disable", {"password": "wrong"}).status_code, 401)
        self.assertEqual(self.post("disable", {"password": PASSWORD}).status_code, 200)
        factor.refresh_from_db()
        self.assertFalse(factor.enabled or factor.secret_ciphertext or factor.pending_ciphertext)
        self.assertFalse(factor.recovery_codes.exists())
        self.assertNotIn(mfa.PROOF_KEY, self.client.session)

    def test_key_mismatch_fails_closed_without_proof(self):
        factor = self.factor()
        factor.secret_ciphertext = "corrupted-ciphertext"
        factor.save(update_fields=["secret_ciphertext"])
        response = self.post("verify", {"code": self.code()})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"]["code"], "mfa_unavailable")
        self.assertNotIn(mfa.PROOF_KEY, self.client.session)

    def test_changing_encryption_key_invalidates_existing_verified_sessions(self):
        self.factor()
        self.authenticate()
        with override_settings(STAFF_MFA_ENCRYPTION_KEY=Fernet.generate_key().decode()):
            self.assertFalse(self.client.get(reverse("staff_mfa:status")).json()["data"]["verified"])
            self.assertEqual(self.client.get("/gestao/").json()["error"]["code"], "mfa_required")
            self.assertEqual(self.post("verify", {"code": self.code()}).status_code, 503)

    def test_one_step_clock_drift_is_accepted_but_older_steps_cannot_be_reused(self):
        self.factor()
        for delta in (-30, 0, 30):
            code = pyotp.TOTP(TEST_SECRET).at(self.now + timedelta(seconds=delta))
            self.assertEqual(self.post("verify", {"code": code}).status_code, 200)
        self.assertEqual(self.post("verify", {"code": self.code()}).status_code, 401)

    def test_future_proof_timestamp_cannot_bypass_expiry(self):
        self.factor()
        self.authenticate()
        session = self.client.session
        session[mfa.PROOF_KEY]["at"] = self.now.timestamp() + 60
        session.save()
        self.assertFalse(self.client.get(reverse("staff_mfa:status")).json()["data"]["verified"])

    def test_stale_proof_cannot_manage_device_or_recovery_codes(self):
        factor = self.factor()
        mfa.new_recovery_codes(factor)
        self.authenticate()
        session = self.client.session
        session[mfa.PROOF_KEY]["at"] -= settings.STAFF_MFA_RECENT_AGE + 1
        session.save()
        data = self.client.get(reverse("staff_mfa:status")).json()["data"]
        self.assertTrue(data["verified"])
        self.assertFalse(data["recently_verified"])
        for name in ("recovery_codes", "disable", "enroll"):
            self.assertEqual(self.post(name, {"password": PASSWORD}).status_code, 403)
        self.assertEqual(factor.recovery_codes.count(), 10)

    def test_recovery_allows_replacing_phone_and_invalidates_old_codes_and_sessions(self):
        factor = self.factor()
        codes = mfa.new_recovery_codes(factor)
        self.assertEqual(self.post("verify", {"code": codes[0], "method": "recovery"}).status_code, 200)
        other = Client()
        other.force_login(self.actor)
        self.assertEqual(self.post("verify", {"code": codes[1], "method": "recovery"}, client=other).status_code, 200)
        data = self.post("enroll", {"password": PASSWORD}).json()["data"]
        self.assertEqual(self.post("confirm", {"code": pyotp.TOTP(data["secret"]).at(self.now)}).status_code, 200)
        factor.refresh_from_db()
        self.assertTrue(mfa.decrypt(factor.secret_ciphertext) == data["secret"])
        self.assertEqual(self.post("verify", {"code": codes[2], "method": "recovery"}).status_code, 401)
        self.assertEqual(other.get("/gestao/").json()["error"]["code"], "mfa_required")

    def test_password_hash_upgrade_does_not_break_enrollment_session(self):
        self.actor.password = PBKDF2PasswordHasher().encode(PASSWORD, "synthetic-salt", iterations=1)
        self.actor.save(update_fields=["password"])
        self.client.force_login(self.actor)
        data = self.post("enroll", {"password": PASSWORD}).json()["data"]
        response = self.post("confirm", {"code": pyotp.TOTP(data["secret"]).at(self.now)})
        self.assertEqual(response.status_code, 200)

    def test_audit_failure_in_confirm_preserves_pending_and_does_not_enable_factor(self):
        data = self.post("enroll", {"password": PASSWORD}).json()["data"]
        with patch("accounts.mfa.audit", side_effect=DatabaseError("Synthetic failure")):
            response = self.post("confirm", {"code": pyotp.TOTP(data["secret"]).at(self.now)})
        self.assertEqual(response.status_code, 503)
        factor = StaffMFA.objects.get(user=self.actor)
        self.assertFalse(factor.enabled)
        self.assertTrue(bool(factor.pending_ciphertext))
        self.assertFalse(factor.recovery_codes.exists())
        self.assertNotIn(mfa.PROOF_KEY, self.client.session)

    def test_recovery_rotation_invalidates_other_verified_sessions(self):
        factor = self.factor()
        codes = mfa.new_recovery_codes(factor)
        self.assertEqual(self.post("verify", {"code": codes[0], "method": "recovery"}).status_code, 200)
        other = Client()
        other.force_login(self.actor)
        self.assertEqual(self.post("verify", {"code": codes[1], "method": "recovery"}, client=other).status_code, 200)
        self.assertEqual(self.post("recovery_codes", {"password": PASSWORD}).status_code, 200)
        self.assertEqual(other.get("/gestao/").json()["error"]["code"], "mfa_required")
        self.assertTrue(self.client.get(reverse("staff_mfa:status")).json()["data"]["verified"])

    def test_audit_failure_rolls_back_totp_consumption_and_session_grant(self):
        factor = self.factor()
        with patch("accounts.mfa.audit", side_effect=DatabaseError("Synthetic failure")):
            response = self.post("verify", {"code": self.code()})
        self.assertEqual(response.status_code, 503)
        factor.refresh_from_db()
        self.assertEqual(factor.last_step, -1)
        self.assertNotIn(mfa.PROOF_KEY, self.client.session)

    def test_audit_failure_rolls_back_recovery_code_consumption(self):
        factor = self.factor()
        code = mfa.new_recovery_codes(factor)[0]
        with patch("accounts.mfa.audit", side_effect=DatabaseError("Synthetic failure")):
            self.assertEqual(self.post("verify", {"code": code, "method": "recovery"}).status_code, 503)
        self.assertEqual(factor.recovery_codes.filter(used_at__isnull=False).count(), 0)
        self.assertNotIn(mfa.PROOF_KEY, self.client.session)

    def test_audit_failure_does_not_start_enrollment(self):
        with patch("accounts.mfa.audit", side_effect=DatabaseError("Synthetic failure")):
            self.assertEqual(self.post("enroll", {"password": PASSWORD}).status_code, 503)
        self.assertFalse(StaffMFA.objects.exists())
        self.assertNotIn(mfa.CHALLENGE_KEY, self.client.session)

    def test_session_save_failure_rolls_back_factor_and_audit(self):
        factor = self.factor()
        with patch("django.contrib.sessions.backends.db.SessionStore.save", side_effect=DatabaseError("Synthetic failure")):
            response = self.post("verify", {"code": self.code()})
        self.assertEqual(response.status_code, 503)
        factor.refresh_from_db()
        self.assertEqual(factor.last_step, -1)
        self.assertFalse(AuditEntry.objects.filter(action="security.mfa_verified").exists())

    def test_payloads_and_methods_are_strict_and_csrf_errors_are_json(self):
        for name, data in (("enroll", {"password": PASSWORD, "user": "injected"}), ("verify", {"code": self.code(), "actor": "injected"})):
            self.assertEqual(self.post(name, data).status_code, 400)
        self.assertEqual(self.post("verify", {"code": self.code(), "method": "sms"}).status_code, 422)
        self.assertEqual(self.post("confirm", {"code": "１２３４５６"}).status_code, 422)
        self.assertEqual(self.client.get(reverse("staff_mfa:verify")).status_code, 405)
        self.assertEqual(self.client.get(reverse("staff_mfa:status"), {"user": "injected"}).status_code, 400)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.actor)
        response = self.post("enroll", {"password": PASSWORD}, client=client)
        self.assertEqual(response.json()["error"]["code"], "csrf_failed")
        token = client.get(reverse("staff_mfa:status")).json()["data"]["csrf_token"]
        response = self.post("enroll", {"password": PASSWORD}, client=client, HTTP_X_CSRFTOKEN=token, HTTP_ORIGIN="https://foreign.invalid")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.post("enroll", {"password": PASSWORD}, client=client, HTTP_X_CSRFTOKEN=token).status_code, 200)

    def test_technical_reset_requires_confirmation_is_audited_and_invalidates_proof(self):
        factor = self.factor()
        mfa.new_recovery_codes(factor)
        self.authenticate()
        with self.assertRaises(CommandError):
            call_command("reset_staff_mfa", str(self.actor.pk), reason="Identity confirmed", stdout=io.StringIO())
        call_command("reset_staff_mfa", str(self.actor.pk), reason="Identity confirmed", confirm=True, stdout=io.StringIO())
        factor.refresh_from_db()
        self.assertFalse(factor.enabled or factor.secret_ciphertext)
        self.assertFalse(factor.recovery_codes.exists())
        self.assertIsNone(AuditEntry.objects.get(action="security.mfa_reset").actor_id)

    @override_settings(ACCOUNT_RATE_LIMITS={**LIMITS, "mfa_code": {"ip": (30, 300), "identity": (2, 300)}})
    def test_rate_limit_is_shared_across_sessions_code_routes_and_client_ips(self):
        self.factor()
        self.assertEqual(self.post("verify", {"code": self.bad_code()}).status_code, 401)
        other = Client()
        other.force_login(self.actor)
        self.assertEqual(self.post("verify", {"code": self.bad_code()}, client=other, REMOTE_ADDR="192.0.2.2").status_code, 401)
        response = self.post("confirm", {"code": "123456"}, client=other, REMOTE_ADDR="192.0.2.3")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.json()["error"]["code"], "rate_limited")
        self.assertIn("Retry-After", response)
        self.assertIn("no-store", response["Cache-Control"])
        self.assertFalse(any(self.actor.email in key for key in AuthThrottleBucket.objects.values_list("key", flat=True)))
