from io import StringIO

from django.conf import settings
from django.core import mail
from django.core.management import call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone, translation

from accounts.models import AccountEmail, ParticipantProfile, User
from accounts.services import request_account_email
from registrations.models import Registration, RegistrationCategory
from registrations.services import register
from registrations.tests import factories
from django.db import IntegrityError, transaction


class ParticipantLanguageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("language@example.org", "Synthetic-language-password-47!", full_name="<script>Pessoa</script>", email_verified_at=timezone.now())
        ParticipantProfile.objects.create(user=cls.user, preferred_language="es")

    def test_browser_languages_translate_all_public_account_forms_and_labels(self):
        for language, title, name in [("pt-br", "Criar conta", "Nome completo"), ("en", "Create account", "Full name"), ("es", "Crear cuenta", "Nombre completo")]:
            response = self.client.get(reverse("accounts:signup"), HTTP_ACCEPT_LANGUAGE=language)
            self.assertContains(response, title)
            self.assertContains(response, name)
            self.assertContains(response, f'<html lang="{language}">')
            self.assertEqual(response["Content-Language"], language)
        for language, title in [("en", "Recover password"), ("es", "Recuperar contraseña")]:
            response = self.client.get(reverse("accounts:request_reset"), HTTP_ACCEPT_LANGUAGE=language)
            self.assertContains(response, title)

    def test_language_cookie_works_before_signup(self):
        response = self.client.post(reverse("accounts:set_language"), {"language": "en", "next": reverse("accounts:signup")})
        self.assertRedirects(response, reverse("accounts:signup"))
        self.assertTrue(response.cookies[settings.LANGUAGE_COOKIE_NAME]["httponly"])
        self.assertEqual(response.cookies[settings.LANGUAGE_COOKIE_NAME]["samesite"], "Lax")
        self.assertContains(self.client.get(reverse("accounts:signup")), "Create account")

    def test_saved_profile_language_wins_on_another_browser_after_login(self):
        self.client.force_login(self.user)
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = "en"
        response = self.client.get(reverse("accounts:dashboard"), HTTP_ACCEPT_LANGUAGE="en")
        self.assertContains(response, "Mi cuenta")
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>")
        self.assertEqual(response["Content-Language"], "es")

    def test_selector_changes_only_current_users_profile_and_survives_new_browser(self):
        other = User.objects.create_user("other-language@example.org", full_name="Outra pessoa")
        ParticipantProfile.objects.create(user=other, preferred_language="pt-br")
        self.client.force_login(self.user)
        self.client.post(reverse("accounts:set_language"), {"language": "en", "user": other.pk, "next": reverse("accounts:dashboard")})
        self.user.profile.refresh_from_db()
        other.profile.refresh_from_db()
        self.assertEqual(self.user.profile.preferred_language, "en")
        self.assertEqual(other.profile.preferred_language, "pt-br")
        fresh = Client()
        fresh.force_login(self.user)
        self.assertContains(fresh.get(reverse("accounts:dashboard")), "My account")

    def test_unsupported_language_and_external_redirect_are_rejected(self):
        self.assertEqual(self.client.get(reverse("accounts:set_language")).status_code, 405)
        self.assertEqual(self.client.post(reverse("accounts:set_language"), {"language": "fr"}).status_code, 400)
        for target in ["https://attacker.example.org", "//attacker.example.org", "javascript:alert(1)", "\\\\attacker.example.org"]:
            response = self.client.post(reverse("accounts:set_language"), {"language": "en", "next": target})
            self.assertEqual(response["Location"], reverse("accounts:login"))

    @override_settings(LANGUAGE_COOKIE_SECURE=True)
    def test_language_cookie_can_be_secure_in_production(self):
        response = self.client.post(reverse("accounts:set_language"), {"language": "en"})
        self.assertTrue(response.cookies[settings.LANGUAGE_COOKIE_NAME]["secure"])

    def test_csrf_is_required_for_language_change(self):
        self.assertEqual(Client(enforce_csrf_checks=True).post(reverse("accounts:set_language"), {"language": "en"}).status_code, 403)

    def test_signup_persists_selected_language_in_profile_and_email_queue(self):
        self.client.post(reverse("accounts:set_language"), {"language": "en"})
        password = "Synthetic-new-language-password-47!"
        response = self.client.post(reverse("accounts:signup"), {
            "email": "new-language@example.org", "full_name": "New person", "password1": password, "password2": password,
            "preferred_language": "fr",  # The server uses the selected supported UI language.
        })
        self.assertRedirects(response, reverse("accounts:signup_done"))
        user = User.objects.get(email="new-language@example.org")
        self.assertEqual(user.profile.preferred_language, "en")
        self.assertEqual(AccountEmail.objects.get(token__user=user).language, "en")

    def test_password_and_form_errors_are_translated(self):
        response = self.client.post(reverse("accounts:signup"), {
            "full_name": "Test", "email": "test@example.org", "password1": "abc", "password2": "different",
        }, HTTP_ACCEPT_LANGUAGE="es")
        self.assertContains(response, "Las contraseñas no coinciden")
        self.assertContains(response, "12")
        response = self.client.post(reverse("accounts:login"), {"username": "absent@example.org", "password": "wrong"}, HTTP_ACCEPT_LANGUAGE="en")
        self.assertContains(response, "Invalid email or password")

    def test_profile_change_applies_new_language_after_redirect(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse("accounts:profile"), {
            "full_name": "Person", "institution": "Institution", "country": "BR", "preferred_language": "en",
        }, follow=True)
        self.assertContains(response, "My profile")
        self.assertContains(response, "Profile updated.")

    def test_email_language_is_frozen_and_worker_language_does_not_leak(self):
        request_account_email(self.user.email, "reset")
        job = AccountEmail.objects.get()
        self.assertEqual(job.language, "es")
        ParticipantProfile.objects.filter(user=self.user).update(preferred_language="en")
        with translation.override("en"):
            call_command("send_account_emails", stdout=StringIO())
            self.assertEqual(translation.get_language(), "en")
        self.assertEqual(mail.outbox[-1].subject, "Recupere su contraseña — CHAGS 14")
        self.assertIn("El código solo se puede utilizar una vez", mail.outbox[-1].body)
        self.assertNotIn("#token=", mail.outbox[-1].subject)

    def test_confirmation_email_uses_english_for_selected_language(self):
        user = User.objects.create_user("confirmation-language@example.org", "Synthetic-language-password-47!", full_name="Person")
        ParticipantProfile.objects.create(user=user, preferred_language="en")
        request_account_email(user.email, "confirm")
        call_command("send_account_emails", stdout=StringIO())
        self.assertEqual(mail.outbox[-1].subject, "Confirm your email — CHAGS 14")
        self.assertIn("The code can only be used once", mail.outbox[-1].body)

    @override_settings(ACCOUNT_RATE_LIMITS={"login": {"ip": (0, 60)}})
    def test_rate_limit_page_is_translated(self):
        response = self.client.post(reverse("accounts:login"), HTTP_ACCEPT_LANGUAGE="en")
        self.assertContains(response, "Please wait", status_code=429)

    def test_registration_and_jems_notices_are_translated(self):
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("registrations:mine")), "Las inscripciones aún no están disponibles")
        self.assertContains(self.client.get(reverse("registrations:jems")), "La cuenta de JEMS es independiente")

    def test_translated_category_is_frozen_and_money_does_not_change(self):
        event = factories.event()
        price = factories.price(event)
        category = price.category
        category.name_en, category.name_es = "Original English category", "Categoría española original"
        category.save()
        obj, _ = register(user=self.user, event_id=event.pk, price_id=price.pk)
        self.client.force_login(self.user)
        response = self.client.get(reverse("registrations:mine"))
        self.assertContains(response, "Categoría española original")
        category.name_es = "Descripción modificada"
        category.save()
        self.assertContains(self.client.get(reverse("registrations:mine")), "Categoría española original")
        obj.refresh_from_db()
        self.assertEqual(obj.amount, price.amount)
        with translation.override("en"):
            self.assertEqual(obj.localized_category_name, "Original English category")
            self.assertEqual(obj.get_status_display(), "Awaiting requirements")
        with self.assertRaises(IntegrityError), transaction.atomic():
            Registration.objects.filter(pk=obj.pk).update(category_name_es="Modified snapshot")

    def test_category_without_translation_uses_original_name(self):
        event = factories.event()
        price = factories.price(event)
        obj, _ = register(user=self.user, event_id=event.pk, price_id=price.pk)
        with translation.override("en"):
            self.assertEqual(obj.localized_category_name, price.category.name_pt)

    def test_price_options_use_selected_language_before_registration(self):
        event = factories.event()
        price = factories.price(event)
        RegistrationCategory.objects.filter(pk=price.category_id).update(name_es="Categoría española disponible")
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse("registrations:mine")), "Categoría española disponible")
