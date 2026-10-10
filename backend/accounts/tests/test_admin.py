from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import TestCase
from django.urls import reverse

from accounts.forms import AccountCreationForm

User = get_user_model()


class AccountAdminTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_superuser(
            "admin@example.org", "Synthetic-test-password-42!", full_name="Administrador"
        )
        cls.participant = User.objects.create_user(
            "person@example.org", "Synthetic-test-password-42!", full_name="Pessoa"
        )
        cls.staff = User.objects.create_user(
            "staff@example.org", "Synthetic-test-password-42!", full_name="Equipe", is_staff=True
        )
        cls.staff.user_permissions.set(
            Permission.objects.filter(content_type__model__in=["user", "group"])
        )
        cls.group = Group.objects.create(name="Equipe de teste")
        cls.staff.groups.add(cls.group)

    def test_anonymous_and_participant_cannot_enter_admin(self):
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)
        self.client.force_login(self.participant)
        self.assertEqual(self.client.get(reverse("admin:index")).status_code, 302)

    def test_staff_cannot_manage_users_or_groups_even_with_model_permissions(self):
        self.client.force_login(self.staff)
        for route in (
            reverse("admin:accounts_user_changelist"),
            reverse("admin:accounts_user_add"),
            reverse("admin:accounts_user_change", args=[self.staff.pk]),
            reverse("admin:auth_group_change", args=[self.group.pk]),
        ):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 403)
        response = self.client.post(
            reverse("admin:accounts_user_change", args=[self.staff.pk]),
            {"email": self.staff.email, "full_name": "Equipe", "is_superuser": "on"},
        )
        self.assertEqual(response.status_code, 403)
        self.staff.refresh_from_db()
        self.assertFalse(self.staff.is_superuser)

    def test_superuser_can_view_add_change_and_password_forms(self):
        self.client.force_login(self.admin)
        for route in (
            reverse("admin:accounts_user_changelist"),
            reverse("admin:accounts_user_add"),
            reverse("admin:accounts_user_change", args=[self.participant.pk]),
            reverse("admin:auth_user_password_change", args=[self.participant.pk]),
        ):
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 200)

    def test_superuser_creates_account_without_granting_admin_rights(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("admin:accounts_user_add"),
            {
                "email": "NEW@EXAMPLE.ORG",
                "full_name": "Nova Pessoa",
                "usable_password": "true",
                "password1": "Synthetic-new-password-97!",
                "password2": "Synthetic-new-password-97!",
            },
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(email="new@example.org")
        self.assertTrue(user.check_password("Synthetic-new-password-97!"))
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)

    def test_form_rejects_duplicate_email_and_weak_password(self):
        for email, password in (
            ("PERSON@EXAMPLE.ORG", "Synthetic-new-password-97!"),
            ("new@example.org", "123"),
        ):
            with self.subTest(email=email):
                form = AccountCreationForm(
                    data={
                        "email": email,
                        "full_name": "Nova Pessoa",
                        "usable_password": "true",
                        "password1": password,
                        "password2": password,
                    }
                )
                self.assertFalse(form.is_valid())

    def test_admin_login_uses_email_and_sets_session(self):
        response = self.client.post(
            reverse("admin:login"),
            {"username": "ADMIN@EXAMPLE.ORG", "password": "Synthetic-test-password-42!"},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.client.session.get("_auth_user_id"), str(self.admin.pk))

    def test_admin_login_requires_csrf(self):
        from django.test import Client

        client = Client(enforce_csrf_checks=True)
        response = client.post(
            reverse("admin:login"),
            {"username": self.admin.email, "password": "Synthetic-test-password-42!"},
        )
        self.assertEqual(response.status_code, 403)
