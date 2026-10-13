import io
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase
import psycopg

from accounts.models import User
from operations import database_backups as backups
from registrations.services import register
from registrations.tests import factories


class DatabaseBackupTests(TransactionTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.user = factories.user()
        self.event = factories.event()
        self.price = factories.price(self.event)
        self.registration, _ = register(user=self.user, event_id=self.event.pk, price_id=self.price.pk)

    def backup(self):
        return backups.create_backup(output_dir=self.directory)

    def temporary_databases(self):
        parameters = backups.connection_parameters("default")
        with psycopg.connect(**{**parameters, "dbname": "postgres"}) as connection:
            return connection.execute("SELECT datname FROM pg_database WHERE datname LIKE 'chags_restore_%' ORDER BY datname").fetchall()

    def test_restore_preserves_participant_and_registration_without_touching_source(self):
        bundle = self.backup()
        before = self.temporary_databases()
        inventory = backups.database_inventory

        def inspect_restored(connection):
            row = connection.execute("SELECT id, email, password FROM accounts_user").fetchone()
            self.assertEqual(row, (self.user.pk, self.user.email, self.user.password))
            row = connection.execute("SELECT id, amount, currency, category_name FROM registrations_registration").fetchone()
            self.assertEqual(row, (self.registration.pk, self.registration.amount,
                                   self.registration.currency, self.registration.category_name))
            return inventory(connection)

        with patch.object(backups, "database_inventory", side_effect=inspect_restored):
            self.assertGreater(backups.verify_backup(bundle=bundle), 10)
        self.assertEqual(before, self.temporary_databases())
        self.user.refresh_from_db()
        self.registration.refresh_from_db()
        self.assertEqual(User.objects.count(), 1)
        manifest = json.loads((bundle / "manifest.json").read_text())
        self.assertIn("price_no_active_overlap", [row[1] for row in manifest["inventory"]["constraints"]])
        self.assertTrue(manifest["inventory"]["triggers"])
        self.assertNotIn(self.user.email, (bundle / "manifest.json").read_text())

    def test_checksum_failure_does_not_connect_or_create_a_database(self):
        bundle = self.backup()
        with (bundle / "database.dump").open("ab") as stream:
            stream.write(b"corruption")
        with patch.object(backups.psycopg, "connect") as connect:
            with self.assertRaisesMessage(CommandError, "Checksum divergente"):
                backups.verify_backup(bundle=bundle)
            connect.assert_not_called()

    def test_invalid_manifest_is_rejected_before_connecting(self):
        (self.directory / "database.dump").write_bytes(b"invalid")
        (self.directory / "manifest.json").write_text('{"format": true}')
        with patch.object(backups.psycopg, "connect") as connect:
            with self.assertRaisesMessage(CommandError, "Manifesto de backup inválido"):
                backups.verify_backup(bundle=self.directory)
            connect.assert_not_called()

    def test_wrong_restore_client_version_cannot_create_a_database(self):
        bundle = self.backup()
        with patch.object(backups, "client_major", return_value=18):
            with patch.object(backups.psycopg, "connect") as connect:
                with self.assertRaisesMessage(CommandError, "mesma versão principal"):
                    backups.verify_backup(bundle=bundle)
                connect.assert_not_called()

    def test_failed_restore_removes_only_its_temporary_database(self):
        bundle = self.backup()
        before = self.temporary_databases()
        run = backups.run_client

        def fail_restore(tool, arguments, environment=None):
            if tool.stem == "pg_restore" and "--version" not in arguments:
                raise CommandError("Falha simulada de restauração.")
            return run(tool, arguments, environment)

        with patch.object(backups, "run_client", side_effect=fail_restore):
            with self.assertRaisesMessage(CommandError, "Falha simulada"):
                backups.verify_backup(bundle=bundle)
        self.assertEqual(before, self.temporary_databases())
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_inventory_mismatch_fails_and_still_removes_temporary_database(self):
        bundle = self.backup()
        path = bundle / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["inventory"]["tables"]["accounts_user"] += 1
        path.write_text(json.dumps(manifest))
        before = self.temporary_databases()
        with self.assertRaisesMessage(CommandError, "divergiu"):
            backups.verify_backup(bundle=bundle)
        self.assertEqual(before, self.temporary_databases())

    def test_failed_dump_leaves_no_incomplete_bundle(self):
        run = backups.run_client

        def fail_dump(tool, arguments, environment=None):
            if tool.stem == "pg_dump" and "--version" not in arguments:
                raise CommandError("Falha simulada de backup.")
            return run(tool, arguments, environment)

        with patch.object(backups, "run_client", side_effect=fail_dump):
            with self.assertRaisesMessage(CommandError, "Falha simulada"):
                self.backup()
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_backup_uses_exported_snapshot_even_when_new_data_arrives(self):
        run = backups.run_client
        inserted = False

        def insert_before_dump(tool, arguments, environment=None):
            nonlocal inserted
            if tool.stem == "pg_dump" and "--version" not in arguments and not inserted:
                inserted = True
                factories.user("snapshot-later@example.org")
            return run(tool, arguments, environment)

        with patch.object(backups, "run_client", side_effect=insert_before_dump):
            bundle = self.backup()
        self.assertEqual(User.objects.count(), 2)
        self.assertEqual(json.loads((bundle / "manifest.json").read_text())["inventory"]["tables"]["accounts_user"], 1)
        backups.verify_backup(bundle=bundle)
        self.assertEqual(User.objects.count(), 2)

    def test_commands_create_distinct_bundles_and_verify_them(self):
        output = io.StringIO()
        call_command("backup_database", output_dir=str(self.directory), stdout=output)
        first = next(self.directory.iterdir())
        call_command("backup_database", output_dir=str(self.directory), stdout=output)
        self.assertEqual(len(list(self.directory.iterdir())), 2)
        call_command("verify_database_backup", str(first), stdout=output)
        self.assertIn("banco temporário removido", output.getvalue())
        if os.name != "nt":
            self.assertEqual(first.stat().st_mode & 0o777, 0o700)
            self.assertEqual((first / "database.dump").stat().st_mode & 0o777, 0o600)
            self.assertEqual((first / "manifest.json").stat().st_mode & 0o777, 0o600)

    def test_client_password_file_is_private_ephemeral_and_absent_from_arguments(self):
        parameters = {"dbname": "sample", "user": "sample", "password": "fake:password\\test",
                      "host": "127.0.0.1", "sslmode": "verify-full"}
        with patch.dict(os.environ, {"PGPASSWORD": "fake-inherited", "PGSSLMODE": "disable"}):
            with backups.client_connection(parameters) as (connection_string, environment):
                passfile = Path(environment["PGPASSFILE"])
                self.assertNotIn(parameters["password"], connection_string)
                self.assertNotIn("PGPASSWORD", environment)
                self.assertNotIn("PGSSLMODE", environment)
                self.assertIn("verify-full", connection_string)
                self.assertEqual(passfile.read_text(), "*:*:*:*:fake\\:password\\\\test\n")
                if os.name != "nt":
                    self.assertEqual(passfile.stat().st_mode & 0o777, 0o600)
        self.assertFalse(passfile.exists())
