from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID

from django.contrib.auth import authenticate, get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, connections, transaction
from django.test import TestCase, TransactionTestCase

User = get_user_model()


class UserTests(TestCase):
    def test_user_has_uuid_hashed_password_and_no_administrative_access(self):
        user = User.objects.create_user(
            email="  Person.Name+Event@EXAMPLE.ORG  ",
            password="Synthetic-test-password-42!",
            full_name="  Pessoa de Teste  ",
        )
        self.assertIsInstance(user.pk, UUID)
        self.assertEqual(user.email, "person.name+event@example.org")
        self.assertEqual(user.full_name, "Pessoa de Teste")
        self.assertTrue(user.check_password("Synthetic-test-password-42!"))
        self.assertNotEqual(user.password, "Synthetic-test-password-42!")
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertIsNone(user.email_verified_at)

    def test_required_identity_fields_are_validated(self):
        for email, name, error in (
            ("", "Pessoa", ValueError),
            ("  ", "Pessoa", ValueError),
            ("invalid", "Pessoa", ValidationError),
            ("person@example.org", "  ", ValidationError),
        ):
            with self.subTest(email=email, name=name):
                with self.assertRaises(error):
                    User.objects.create_user(email, full_name=name)
        self.assertEqual(User.objects.count(), 0)

    def test_email_authentication_ignores_case_and_rejects_inactive_accounts(self):
        user = User.objects.create_user(
            "person@example.org", "Synthetic-test-password-42!", full_name="Pessoa"
        )
        self.assertEqual(
            authenticate(email=" PERSON@EXAMPLE.ORG ", password="Synthetic-test-password-42!"),
            user,
        )
        self.assertIsNone(authenticate(email=user.email, password="wrong-password"))
        user.is_active = False
        user.save(update_fields=["is_active"])
        self.assertIsNone(authenticate(email=user.email, password="Synthetic-test-password-42!"))

    def test_passwordless_accounts_cannot_authenticate(self):
        user = User.objects.create_user("person@example.org", full_name="Pessoa")
        self.assertFalse(user.has_usable_password())
        self.assertIsNone(authenticate(email=user.email, password=""))

    def test_superuser_requires_admin_flags_and_password(self):
        for extra in ({"is_staff": False}, {"is_superuser": False}):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                User.objects.create_superuser(
                    "admin@example.org", "Synthetic-test-password-42!", full_name="Admin", **extra
                )
        with self.assertRaises(ValueError):
            User.objects.create_superuser("admin@example.org", full_name="Admin")
        user = User.objects.create_superuser(
            "admin@example.org", "Synthetic-test-password-42!", full_name="Admin"
        )
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)

    def test_database_rejects_case_variant_even_when_model_validation_is_bypassed(self):
        User.objects.create_user("person@example.org", full_name="Pessoa")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.bulk_create([User(email="PERSON@EXAMPLE.ORG", full_name="Outra")])
        self.assertEqual(User.objects.count(), 1)

    def test_database_rejects_empty_email(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.bulk_create([User(email="", full_name="Pessoa")])


class ConcurrentEmailTests(TransactionTestCase):
    def test_two_concurrent_inserts_cannot_create_case_variant_duplicates(self):
        self.assertEqual(connection.vendor, "postgresql")
        barrier = Barrier(2)

        def insert(email):
            try:
                barrier.wait(timeout=10)
                with transaction.atomic():
                    # bulk_create deliberately bypasses normalization/validation:
                    # uniqueness must also hold inside the real database.
                    User.objects.bulk_create([User(email=email, full_name="Pessoa", password="!")])
                return "created"
            except IntegrityError:
                return "duplicate"
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(insert, ["person@example.org", "PERSON@EXAMPLE.ORG"]))
        self.assertCountEqual(results, ["created", "duplicate"])
        self.assertEqual(User.objects.count(), 1)
