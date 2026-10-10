from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse


class PortalIntegrationTests(TestCase):
    def test_existing_public_pages_are_preserved_and_served(self):
        for url, filename in (("/", "index.html"), ("/pt-br/", "pt-br/index.html"), ("/pt-br/inscricoes.html", "pt-br/inscricoes.html")):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            content = b"".join(response.streaming_content)
            self.assertEqual(content, (settings.BASE_DIR.parent / filename).read_bytes())

    def test_legacy_login_and_submission_paths_lead_to_real_protected_flow(self):
        self.assertRedirects(self.client.get("/sistema-login.html"), reverse("accounts:login"))
        self.assertRedirects(self.client.get("/pt-br/sistema-login.html"), reverse("accounts:login"))
        response = self.client.get("/pt-br/sistema-submissao.html", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "E-mail")
        self.assertNotContains(response, "Nova Submissão de Trabalho")

    def test_only_public_assets_are_exposed_by_static_finders(self):
        self.assertEqual(Path(finders.find("portal/style.css")), settings.BASE_DIR.parent / "style.css")
        self.assertTrue(finders.find("accounts/account.js"))
        for path in ("portal/backend/.env", "portal/backend/.local/django-secret-key", "portal/README.md", "portal/../backend/manage.py"):
            self.assertIsNone(finders.find(path))
        self.assertRedirects(self.client.get("/style.css"), "/static/portal/style.css", fetch_redirect_response=False)

    def test_private_and_unknown_paths_are_not_served_as_public_files(self):
        for path in ("/backend/.local/django-secret-key", "/backend/.env", "/README.md", "/unknown.html", "/pt-br/unknown.html"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)

    def test_confirmation_and_reset_pages_use_local_script_and_no_token_query(self):
        for name in ("accounts:confirm", "accounts:reset"):
            response = self.client.get(reverse(name))
            self.assertContains(response, 'id="id_token"')
            self.assertContains(response, "/static/accounts/account.js")
            self.assertIn("no-store", response["Cache-Control"])
