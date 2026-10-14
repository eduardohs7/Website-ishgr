import os
import tempfile
from datetime import datetime, timezone as utc_timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase
import io

from accounts.mfa import matching_step
from config.settings.environment import mfa_encryption_key, staff_mfa_required
from .test_mfa import TEST_SECRET


class MFACryptoTests(SimpleTestCase):
    def test_rfc6238_sha1_vector_and_counter_replay_protection(self):
        with patch("accounts.mfa.timezone.now", return_value=datetime.fromtimestamp(59, utc_timezone.utc)):
            self.assertEqual(matching_step(TEST_SECRET, "287082", -1), 1)
            self.assertIsNone(matching_step(TEST_SECRET, "287082", 1))
            self.assertIsNone(matching_step(TEST_SECRET, "１２３４５６", -1))

    def test_development_mfa_key_is_private_persistent_and_independent(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"CHAGS_MFA_ENCRYPTION_KEY": ""}):
                first = mfa_encryption_key(Path(directory))
                second = mfa_encryption_key(Path(directory))
            self.assertTrue(first == second)
            Fernet(first.encode())
            if os.name != "nt":
                self.assertEqual((Path(directory) / ".local/mfa-encryption-key").stat().st_mode & 0o777, 0o600)

    def test_missing_and_invalid_production_key_fail_without_exposing_value(self):
        for value in ("", "invalid-synthetic-key"):
            with patch.dict(os.environ, {"CHAGS_MFA_ENCRYPTION_KEY": value}):
                with self.assertRaises(ImproperlyConfigured) as raised:
                    mfa_encryption_key()
            self.assertTrue(not value or value not in str(raised.exception))

    def test_requirement_flag_is_explicit_and_fail_closed(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertTrue(staff_mfa_required(True))
            self.assertFalse(staff_mfa_required(False))
        with patch.dict(os.environ, {"CHAGS_REQUIRE_STAFF_MFA": "bad"}):
            with self.assertRaises(ImproperlyConfigured):
                staff_mfa_required(True)

    def test_key_command_never_prints_key_or_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "private/key"
            output = io.StringIO()
            call_command("create_mfa_key", str(path), stdout=output)
            key = path.read_bytes()
            Fernet(key)
            self.assertTrue(key.decode() not in output.getvalue())
            with self.assertRaises(CommandError):
                call_command("create_mfa_key", str(path), stdout=output)
            self.assertTrue(path.read_bytes() == key)
